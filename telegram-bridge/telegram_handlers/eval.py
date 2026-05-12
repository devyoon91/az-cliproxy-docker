"""`/eval` Telegram command — runs the golden set against AZ + judges with Haiku.

Three modes (#114):

  /eval                  전체 골든셋 실행 + 채점
  /eval <case_id>        단건 실행 + 채점
  /eval baseline         전체 실행 + 결과를 eval/baseline.json 으로 저장

진행 상황은 같은 메시지를 in-place 로 갱신해 표시 (Telegram edit API).
종료 시 통과율 / 비용 / 케이스별 결과를 한 메시지로 정리.

설계 메모:
- 실행/채점은 `eval.runner` / `eval.judge` 의 기존 함수를 그대로 호출 —
  중복 구현 안 함. 이 모듈은 "Telegram I/O + 진행 표시 + 리포트 작성"
  만 책임진다.
- pure helper (`parse_eval_args`, `format_report`, `build_run_summary`,
  `save_baseline`) 를 분리해 telegram-mock 하네스 없이 테스트 가능.
- cmd 핸들러 자체는 today.py 패턴을 따라 mock 하네스 부재로 인해
  unit-test 에서 제외 (실제 통합은 사용자가 /eval 로 한 번 돌려 검증).
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from telegram import Update
from telegram.ext import ContextTypes

# eval/* 는 컨테이너에서 `./eval` 가 /app/eval 로 마운트되었을 때만 import
# 가능. 마운트 누락 시에도 bot.py 전체가 죽지 않도록 wrap — /eval 명령
# 호출 시점에 친절한 에러 메시지로 거절한다.
try:
    from eval.az_client import HTTPAZClient
    from eval.judge import HTTPJudgeClient, judge_run_dir
    from eval.runner import run_case
    from eval.schema import EvalCase, EvalSchemaError, load_case, load_cases
    from eval.trace import Trace, make_run_dir

    _EVAL_AVAILABLE = True
    _EVAL_IMPORT_ERROR: str | None = None
except ImportError as _imp_err:
    _EVAL_AVAILABLE = False
    _EVAL_IMPORT_ERROR = str(_imp_err)
    # 타입 힌트는 `from __future__ import annotations` 가 문자열로 만들어
    # 평가하지 않으므로 EvalCase / Trace 가 정의되어 있지 않아도 함수
    # 시그니처는 문제 없음. parse_eval_args / format_report / save_baseline
    # 같은 pure helper 도 실제 eval 클래스에 접근하지 않으므로 동작 가능.

logger = logging.getLogger(__name__)


# Env / 경로 — bot.py 와 동일한 패턴 (chat_id gate + env override).
_chat_id_raw = os.environ.get("TELEGRAM_CHAT_ID")
CHAT_ID: int | None = int(_chat_id_raw) if _chat_id_raw else None

AZ_API_URL = os.environ.get("AZ_API_URL", "http://agent-zero:80")
TASKS_DIR = Path(os.environ.get("EVAL_TASKS_DIR", "/app/tasks"))
CASES_DIR = Path(os.environ.get("EVAL_CASES_DIR", "/app/eval/cases"))
RUNS_DIR = Path(os.environ.get("EVAL_RUNS_DIR", "/app/eval/runs"))
BASELINE_PATH = Path(
    os.environ.get("EVAL_BASELINE_PATH", "/app/eval/baseline.json")
)

# Telegram 메시지 최대 길이 (4096). 안전 마진 96.
_TG_MSG_MAX = 4000


# ── 인자 파싱 (pure) ────────────────────────────────────────────────────


def parse_eval_args(args: list[str]) -> tuple[str, str | None] | str:
    """`(mode, target_case_id)` 또는 오류 메시지 반환.

    mode: "all" | "single" | "baseline"
    """
    if not args:
        return ("all", None)
    if len(args) > 1:
        return "사용법: /eval [<case_id> | baseline]"
    a = args[0].strip()
    if not a:
        return ("all", None)
    if a == "baseline":
        return ("baseline", None)
    return ("single", a)


# ── 리포트 포맷팅 (pure) ────────────────────────────────────────────────


def format_report(
    traces: list[Trace],
    judge_results: list[Any],  # list[JudgeResult] — 순환 import 회피
    *,
    mode: str,
    elapsed_sec: float,
) -> str:
    """최종 Telegram 메시지 본문 작성.  4000자 초과 시 truncate."""
    judge_by_case = {r.case_id: r for r in judge_results}

    n_total = len(traces)
    n_passed = sum(
        1
        for t in traces
        if not t.guard_violations
        and not t.error
        and judge_by_case.get(t.case_id) is not None
        and not judge_by_case[t.case_id].error
        and judge_by_case[t.case_id].passed
    )
    pass_rate = (n_passed / n_total * 100) if n_total else 0.0

    total_run_cost = sum(t.cost_usd for t in traces)
    total_judge_cost = sum(
        r.cost_usd for r in judge_results
    )

    lines = [
        f"🧪 eval 결과 ({mode})",
        f"통과: {n_passed}/{n_total} ({pass_rate:.0f}%)",
        f"비용: 실행 ${total_run_cost:.4f} + 채점 ${total_judge_cost:.4f}",
        f"소요: {elapsed_sec:.1f}s",
        "",
    ]

    for t in traces:
        jr = judge_by_case.get(t.case_id)
        if t.error:
            mark = "❌"
            detail = f"runner: {t.error[:80]}"
        elif t.guard_violations:
            mark = "⚠️"
            detail = "guard: " + "; ".join(t.guard_violations[:2])
        elif jr is None:
            mark = "❓"
            detail = "no judge result"
        elif jr.error:
            mark = "⚠️"
            detail = f"judge err: {jr.error[:80]}"
        elif jr.passed:
            mark = "✅"
            detail = f"score={jr.score:.2f}"
        else:
            mark = "❌"
            reasoning = (jr.reasoning or "")[:60]
            detail = f"score={jr.score:.2f}  {reasoning}"
        lines.append(f"{mark} {t.case_id}  {detail}")

    text = "\n".join(lines)
    if len(text) > _TG_MSG_MAX:
        text = text[: _TG_MSG_MAX] + "\n…(잘림)"
    return text


# ── summary dict 작성 (pure) ─────────────────────────────────────────────


def build_run_summary(
    traces: list[Trace],
    judge_results: list[Any],
    *,
    run_dir: Path,
    started_at: datetime,
    elapsed_sec: float,
) -> dict:
    """`_summary.json` 및 baseline.json 으로 저장될 dict 를 구성.

    runner.RunSummary.to_summary_dict 보다 풍부 — judge 결과까지 포함.
    CI 회귀 비교(#116)가 이 dict 를 읽고 baseline 과 diff.
    """
    judge_by_case = {r.case_id: r for r in judge_results}

    passed_judges = sum(
        1 for r in judge_results if r.passed and not r.error
    )
    failed_judges = sum(
        1 for r in judge_results if not r.passed and not r.error
    )
    errored = sum(1 for r in judge_results if r.error)
    guard_violations = sum(
        1 for t in traces if t.guard_violations or t.error
    )

    cases = []
    for t in traces:
        jr = judge_by_case.get(t.case_id)
        cases.append(
            {
                "case_id": t.case_id,
                "guard_violations": list(t.guard_violations),
                "runner_error": t.error,
                "run_cost_usd": round(t.cost_usd, 6),
                "judge_cost_usd": round(t.judge_cost_usd, 6),
                "duration_ms": t.duration_ms,
                "turns": t.turns,
                "score": jr.score if jr else None,
                "passed": jr.passed if jr else None,
                "judge_error": jr.error if jr else None,
            }
        )

    return {
        "run_dir": str(run_dir),
        "started_at": started_at.isoformat(),
        "elapsed_sec": round(elapsed_sec, 3),
        "total": len(traces),
        "passed_judges": passed_judges,
        "failed_judges": failed_judges,
        "errored": errored,
        "guard_violations": guard_violations,
        "total_run_cost_usd": round(
            sum(t.cost_usd for t in traces), 6
        ),
        "total_judge_cost_usd": round(
            sum(r.cost_usd for r in judge_results), 6
        ),
        "cases": cases,
    }


# ── baseline 저장 (pure) ────────────────────────────────────────────────


def save_baseline(summary: dict, path: Path) -> None:
    """baseline.json 저장.  부모 디렉토리 없으면 생성."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


# ── 핸들러 ──────────────────────────────────────────────────────────────


async def cmd_eval(
    update: Update, context: ContextTypes.DEFAULT_TYPE
) -> None:
    """`/eval [<case_id> | baseline]`"""
    if update.effective_chat is None or update.effective_chat.id != CHAT_ID:
        return
    msg = update.effective_message
    if msg is None:
        return

    if not _EVAL_AVAILABLE:
        await msg.reply_text(
            f"❌ eval 모듈을 import 할 수 없어 /eval 사용 불가.\n"
            f"docker-compose.yml 의 `./eval:/app/eval` 마운트가 있는지 확인.\n"
            f"원인: {_EVAL_IMPORT_ERROR}"
        )
        return

    parsed = parse_eval_args(list(context.args or []))
    if isinstance(parsed, str):
        await msg.reply_text(parsed)
        return
    mode, target = parsed

    # 케이스 로드.
    try:
        cases = _load_cases_for_mode(mode, target)
    except EvalSchemaError as e:
        await msg.reply_text(f"❌ 케이스 로드 실패: {e}")
        return
    if not cases:
        await msg.reply_text("❌ 실행할 케이스가 없습니다.")
        return

    # judge 키 확인.
    if not os.environ.get("ANTHROPIC_API_KEY"):
        await msg.reply_text(
            "❌ ANTHROPIC_API_KEY 가 설정되지 않아 채점 단계 진행 불가."
        )
        return

    progress = await msg.reply_text(
        f"🧪 eval 시작 ({len(cases)}개 케이스, mode={mode})..."
    )

    started_dt = datetime.now(timezone.utc)  # noqa: UP017
    run_dir = make_run_dir(RUNS_DIR, started_at=started_dt)
    az_client = HTTPAZClient(az_url=AZ_API_URL, tasks_dir=TASKS_DIR)

    # 실행.
    traces: list[Trace] = []
    for i, case in enumerate(cases, 1):
        await _safe_edit(
            progress, f"🧪 [{i}/{len(cases)}] {case.id} 실행 중..."
        )
        trace = await run_case(case, az_client, run_dir=run_dir)
        traces.append(trace)

    # 채점.
    await _safe_edit(
        progress, f"⚖️ 채점 중 ({len(traces)}개 트레이스)..."
    )
    judge_client = HTTPJudgeClient()
    judge_results = await judge_run_dir(run_dir, CASES_DIR, judge_client)

    elapsed_sec = (
        datetime.now(timezone.utc) - started_dt  # noqa: UP017
    ).total_seconds()

    # 회차 summary 저장.
    summary = build_run_summary(
        traces,
        judge_results,
        run_dir=run_dir,
        started_at=started_dt,
        elapsed_sec=elapsed_sec,
    )
    (run_dir / "_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    # baseline 모드 — baseline.json 으로 추가 저장.
    baseline_note = ""
    if mode == "baseline":
        try:
            save_baseline(summary, BASELINE_PATH)
            baseline_note = f"\n📌 baseline 저장: `{BASELINE_PATH}`"
        except Exception as e:
            baseline_note = f"\n⚠️ baseline 저장 실패: {e}"

    # 최종 리포트.
    report = format_report(
        traces, judge_results, mode=mode, elapsed_sec=elapsed_sec
    )
    final = report + baseline_note
    if not await _safe_edit(progress, final):
        # edit 가 (길이 등 사유로) 실패하면 새 메시지로 폴백.
        await msg.reply_text(final)


# ── 핸들러 내부 유틸 ────────────────────────────────────────────────────


def _load_cases_for_mode(
    mode: str, target: str | None
) -> list[EvalCase]:
    """mode 별로 case 목록을 로드.

    "single" 모드에서 target 케이스 파일이 없으면 EvalSchemaError raise —
    호출자가 사용자에게 친절한 메시지로 변환.
    """
    if mode == "single":
        assert target is not None
        case_path = CASES_DIR / f"{target}.yaml"
        if not case_path.is_file():
            raise EvalSchemaError(
                f"case not found: {target} (looked in {case_path})"
            )
        return [load_case(case_path)]
    return load_cases(CASES_DIR)


async def _safe_edit(message, text: str) -> bool:
    """Telegram 메시지 in-place 갱신.  실패해도 흐름을 깨지 않는다.

    실패 사유는 다양 (rate-limit, 메시지가 너무 길거나, 이미 같은 내용,
    오래된 메시지 등). True 면 성공, False 면 호출자가 폴백 필요.
    """
    try:
        await message.edit_text(text)
        return True
    except Exception as e:
        logger.debug(f"edit_text failed (ignored): {e}")
        return False


__all__ = [
    "cmd_eval",
    "parse_eval_args",
    "format_report",
    "build_run_summary",
    "save_baseline",
]
