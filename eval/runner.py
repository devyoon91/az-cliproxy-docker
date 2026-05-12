"""Eval runner — 케이스 한 건 또는 전체를 실행하고 트레이스 JSON 저장.

CLI:
    python -m eval.runner --case <id>     # 단건
    python -m eval.runner --all           # 전체

`--cases-dir` 와 `--runs-dir` 로 디렉토리 override 가능. AZ 엔드포인트는
`--az-url` 또는 env `AZ_API_URL` (기본 http://localhost:50001).
task_report 디렉토리는 `--tasks-dir` 또는 env `EVAL_TASKS_DIR`
(기본 `agent-zero/logs/tasks`).

테스트는 `run_case()` / `run_all()` 를 `FakeAZClient` 와 함께 호출해
HTTP·디스크 의존 없이 동작 검증.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from .az_client import AZClient, HTTPAZClient, RunResult
from .schema import EvalCase, EvalSchemaError, load_case, load_cases
from .trace import Trace, make_run_dir, now_iso, save_trace

logger = logging.getLogger(__name__)


# 디렉토리 기본값. CLI / env 가 override.
_DEFAULT_CASES_DIR = Path("eval/cases")
_DEFAULT_RUNS_DIR = Path("eval/runs")
_DEFAULT_TASKS_DIR = Path("agent-zero/logs/tasks")


@dataclass
class RunSummary:
    """한 회차 실행 결과 요약. /eval 명령·CI 가 이 dict 를 그대로 리포트."""

    run_dir: Path
    started_at: str
    ended_at: str
    total: int
    passed_guards: int  # guard_violations / error 모두 비어있는 케이스 수
    failed_guards: int
    total_cost_usd: float
    total_duration_ms: int
    traces: list[Trace]

    def to_summary_dict(self) -> dict:
        return {
            "run_dir": str(self.run_dir),
            "started_at": self.started_at,
            "ended_at": self.ended_at,
            "total": self.total,
            "passed_guards": self.passed_guards,
            "failed_guards": self.failed_guards,
            "total_cost_usd": round(self.total_cost_usd, 6),
            "total_duration_ms": self.total_duration_ms,
            "cases": [
                {
                    "case_id": t.case_id,
                    "turns": t.turns,
                    "cost_usd": round(t.cost_usd, 6),
                    "duration_ms": t.duration_ms,
                    "guard_violations": list(t.guard_violations),
                    "error": t.error,
                }
                for t in self.traces
            ],
        }


# ── 단일 케이스 실행 ────────────────────────────────────────────────────


async def run_case(
    case: EvalCase, client: AZClient, *, run_dir: Path
) -> Trace:
    """케이스 한 건을 실행, Trace 생성 + JSON 저장.

    가드 위반(턴/비용/타임아웃)은 `trace.guard_violations` 에, 클라이언트
    오류는 `trace.error` 에 기록된다. 둘 다 비어있으면 가드 통과.
    """
    result: RunResult = await client.send_and_wait(
        case.task, timeout_sec=case.timeout_sec
    )

    if result.task_report is None:
        # 송신 실패·타임아웃: 빈 trace + error.
        trace = Trace(
            case_id=case.id,
            task=case.task,
            started_at=result.started_at_iso,
            ended_at=result.ended_at_iso,
            duration_ms=_iso_diff_ms(
                result.started_at_iso, result.ended_at_iso
            ),
            az_task_id=None,
            turns=0,
            tool_calls=[],
            final_response="",
            input_tokens=0,
            output_tokens=0,
            cache_read_tokens=0,
            cache_creation_tokens=0,
            reasoning_tokens=0,
            cost_usd=0.0,
            guard_violations=[],
            error=result.error or "no task_report returned",
        )
    else:
        trace = Trace.from_task_report(
            case_id=case.id,
            task=case.task,
            started_at=result.started_at_iso,
            ended_at=result.ended_at_iso,
            report=result.task_report,
        )
        violations = _check_guards(case, trace)
        if violations:
            trace.guard_violations = violations

    save_trace(trace, run_dir)
    return trace


def _check_guards(case: EvalCase, trace: Trace) -> list[str]:
    """case 의 가드(max_turns / max_cost_usd / timeout) 위반 항목 목록."""
    out: list[str] = []
    if trace.turns > case.max_turns:
        out.append(f"max_turns_exceeded ({trace.turns}>{case.max_turns})")
    if trace.cost_usd > case.max_cost_usd:
        out.append(
            f"max_cost_exceeded (${trace.cost_usd:.4f}>${case.max_cost_usd:.4f})"
        )
    # 타임아웃 가드 — duration_ms 기반. 단, 클라이언트가 자체적으로
    # timeout_sec 으로 끊었으면 task_report 가 없어서 여긴 안 옴.
    if trace.duration_ms > case.timeout_sec * 1000:
        out.append(
            f"timeout_exceeded ({trace.duration_ms}ms>{case.timeout_sec}s)"
        )
    return out


def _iso_diff_ms(start_iso: str, end_iso: str) -> int:
    try:
        s = datetime.fromisoformat(start_iso.replace("Z", "+00:00"))
        e = datetime.fromisoformat(end_iso.replace("Z", "+00:00"))
        return max(0, int((e - s).total_seconds() * 1000))
    except (ValueError, AttributeError):
        return 0


# ── 전체 실행 ───────────────────────────────────────────────────────────


async def run_all(
    cases: list[EvalCase],
    client: AZClient,
    *,
    runs_dir: Path,
) -> RunSummary:
    """주어진 케이스 목록을 직렬 실행 (eval 은 보통 동시성 가치가 작음 —
    AZ 가 한 번에 한 monologue 만 깔끔히 처리하는 게 안전)."""
    started_at = now_iso()
    started_dt = datetime.now(timezone.utc)  # noqa: UP017
    run_dir = make_run_dir(runs_dir, started_at=started_dt)

    traces: list[Trace] = []
    for case in cases:
        logger.info(f"[eval] running case: {case.id}")
        trace = await run_case(case, client, run_dir=run_dir)
        traces.append(trace)

    ended_at = now_iso()

    passed = sum(1 for t in traces if not t.guard_violations and not t.error)
    failed = len(traces) - passed
    total_cost = sum(t.cost_usd for t in traces)
    total_duration = sum(t.duration_ms for t in traces)

    summary = RunSummary(
        run_dir=run_dir,
        started_at=started_at,
        ended_at=ended_at,
        total=len(traces),
        passed_guards=passed,
        failed_guards=failed,
        total_cost_usd=total_cost,
        total_duration_ms=total_duration,
        traces=traces,
    )
    _write_summary(summary)
    return summary


def _write_summary(summary: RunSummary) -> None:
    """`<run_dir>/_summary.json` 으로 회차 요약 저장."""
    path = summary.run_dir / "_summary.json"
    path.write_text(
        json.dumps(summary.to_summary_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


# ── CLI ─────────────────────────────────────────────────────────────────


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="python -m eval.runner",
        description="Run eval golden-set cases against Agent Zero.",
    )
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--case", help="단건 실행 — 케이스 id 또는 YAML 경로")
    g.add_argument("--all", action="store_true", help="cases-dir 의 전체 실행")
    p.add_argument(
        "--cases-dir",
        default=str(_DEFAULT_CASES_DIR),
        help=f"YAML 케이스 디렉토리 (기본 {_DEFAULT_CASES_DIR})",
    )
    p.add_argument(
        "--runs-dir",
        default=str(_DEFAULT_RUNS_DIR),
        help=f"결과 저장 디렉토리 (기본 {_DEFAULT_RUNS_DIR})",
    )
    p.add_argument(
        "--az-url",
        default=os.environ.get("AZ_API_URL", "http://localhost:50001"),
        help="AZ HTTP 엔드포인트 (env AZ_API_URL)",
    )
    p.add_argument(
        "--tasks-dir",
        default=os.environ.get("EVAL_TASKS_DIR", str(_DEFAULT_TASKS_DIR)),
        help=(
            f"AZ task_report JSON 디렉토리 (기본 {_DEFAULT_TASKS_DIR}, "
            "env EVAL_TASKS_DIR)"
        ),
    )
    return p.parse_args(argv)


def _resolve_cases(args: argparse.Namespace) -> list[EvalCase]:
    cases_dir = Path(args.cases_dir)
    if args.all:
        return load_cases(cases_dir)
    # --case: 파일 경로 또는 id
    target = args.case
    p = Path(target)
    if p.is_file():
        return [load_case(p)]
    # id 로 가정 — cases_dir 에서 찾는다
    candidate = cases_dir / f"{target}.yaml"
    if not candidate.is_file():
        raise EvalSchemaError(
            f"case not found: {target} (looked in {candidate})"
        )
    return [load_case(candidate)]


def _print_summary(summary: RunSummary) -> None:
    print(f"\n[eval] run_dir: {summary.run_dir}")
    print(
        f"[eval] {summary.passed_guards}/{summary.total} passed "
        f"(guards) | total cost ${summary.total_cost_usd:.4f} | "
        f"duration {summary.total_duration_ms / 1000:.1f}s"
    )
    for t in summary.traces:
        status = "OK"
        if t.error:
            status = f"ERROR: {t.error}"
        elif t.guard_violations:
            status = "GUARD: " + "; ".join(t.guard_violations)
        print(
            f"  - {t.case_id:30s} turns={t.turns:>2} "
            f"cost=${t.cost_usd:.4f} {t.duration_ms / 1000:>5.1f}s  {status}"
        )


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=os.environ.get("EVAL_LOG_LEVEL", "INFO"),
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    try:
        cases = _resolve_cases(args)
    except EvalSchemaError as e:
        print(f"[eval] failed to load cases: {e}", file=sys.stderr)
        return 2

    if not cases:
        print("[eval] no cases to run", file=sys.stderr)
        return 1

    client = HTTPAZClient(az_url=args.az_url, tasks_dir=args.tasks_dir)
    summary = asyncio.run(
        run_all(cases, client, runs_dir=Path(args.runs_dir))
    )
    _print_summary(summary)
    # CI 관점에서: 가드 실패가 하나라도 있으면 비제로 종료.  세부 baseline
    # 비교(통과율 -10%p 기준 등)는 #116 워크플로우의 책임.
    return 0 if summary.failed_guards == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
