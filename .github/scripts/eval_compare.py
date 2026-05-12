"""Diff one eval-run summary against `eval/baseline.json` and write a
markdown report.  CI 워크플로우(#116) 가 호출하는 stand-alone CLI.

비교 대상:
- 통과율 (pass rate, primary gate — threshold_pp 만큼 떨어지면 회귀)
- 총 비용 (실행 + 채점, 변동률만 보고)
- 케이스별 pass 상태 변화 (newly_failing / newly_passing)

baseline 이 없는 경우(첫 main 머지 등) 는 정상 동료로 처리 — 빈 비교 리포트
출력 후 exit 0.  그래서 baseline.json 이 없는 새 저장소에서도 워크플로우가
지속적 실패하지 않는다.

stdlib only — 같은 Python 으로 별도 의존성 설치 없이 동작한다.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any


def _pass_rate(summary: dict[str, Any]) -> float:
    total = int(summary.get("total", 0) or 0)
    passed = int(summary.get("passed_judges", 0) or 0)
    return (passed / total) if total else 0.0


def _total_cost(summary: dict[str, Any]) -> float:
    return float(
        (summary.get("total_run_cost_usd", 0.0) or 0.0)
        + (summary.get("total_judge_cost_usd", 0.0) or 0.0)
    )


def compare(
    current: dict[str, Any],
    baseline: dict[str, Any],
    threshold_pp: float,
) -> dict[str, Any]:
    """비교 결과를 dict 로 반환.

    `verdict` 가 "regression" 이면 호출자가 exit 1 처리, "ok" 면 통과.
    `delta_pp` 가 음수이면 통과율 하락 — 그 절대값이 `threshold_pp` 보다
    크면 회귀로 본다 (예: threshold_pp=10, delta_pp=-12 → 회귀).
    """
    cur_rate = _pass_rate(current)
    base_rate = _pass_rate(baseline)
    delta_pp = (cur_rate - base_rate) * 100

    cur_cost = _total_cost(current)
    base_cost = _total_cost(baseline)
    cost_delta_pct = (
        ((cur_cost - base_cost) / base_cost * 100) if base_cost else 0.0
    )

    cur_cases = {
        c["case_id"]: c for c in current.get("cases", []) or [] if c.get("case_id")
    }
    base_cases = {
        c["case_id"]: c
        for c in baseline.get("cases", []) or []
        if c.get("case_id")
    }
    newly_failing: list[str] = []
    newly_passing: list[str] = []
    for cid in cur_cases.keys() | base_cases.keys():
        cur_p = bool(cur_cases.get(cid, {}).get("passed"))
        base_p = bool(base_cases.get(cid, {}).get("passed"))
        if base_p and not cur_p:
            newly_failing.append(cid)
        elif not base_p and cur_p:
            newly_passing.append(cid)

    is_regression = delta_pp < -threshold_pp

    return {
        "verdict": "regression" if is_regression else "ok",
        "current": {
            "pass_rate": round(cur_rate, 4),
            "total": current.get("total", 0),
            "passed_judges": current.get("passed_judges", 0),
            "total_cost_usd": round(cur_cost, 6),
        },
        "baseline": {
            "pass_rate": round(base_rate, 4),
            "total": baseline.get("total", 0),
            "passed_judges": baseline.get("passed_judges", 0),
            "total_cost_usd": round(base_cost, 6),
        },
        "delta_pp": round(delta_pp, 2),
        "cost_delta_pct": round(cost_delta_pct, 2),
        "threshold_pp": threshold_pp,
        "newly_failing": sorted(newly_failing),
        "newly_passing": sorted(newly_passing),
    }


def format_markdown(result: dict[str, Any]) -> str:
    """비교 결과를 PR comment / job summary 용 마크다운으로."""
    icon = "❌" if result["verdict"] == "regression" else "✅"
    title = (
        "Eval Regression Detected"
        if result["verdict"] == "regression"
        else "Eval OK"
    )
    cur = result["current"]
    base = result["baseline"]

    lines = [
        f"## {icon} {title}",
        "",
        "| | baseline | current | delta |",
        "|---|---|---|---|",
        (
            f"| 통과율 | {base['pass_rate'] * 100:.0f}% "
            f"({base['passed_judges']}/{base['total']}) | "
            f"{cur['pass_rate'] * 100:.0f}% "
            f"({cur['passed_judges']}/{cur['total']}) | "
            f"{result['delta_pp']:+.1f}pp |"
        ),
        (
            f"| 총 비용 | ${base['total_cost_usd']:.4f} | "
            f"${cur['total_cost_usd']:.4f} | "
            f"{result['cost_delta_pct']:+.1f}% |"
        ),
        "",
        f"임계값: 통과율 -{result['threshold_pp']}pp 이상 하락 시 회귀.",
    ]
    if result["newly_failing"]:
        lines.append("")
        lines.append("### 🔴 새로 실패한 케이스")
        for cid in result["newly_failing"]:
            lines.append(f"- `{cid}`")
    if result["newly_passing"]:
        lines.append("")
        lines.append("### 🟢 새로 통과한 케이스")
        for cid in result["newly_passing"]:
            lines.append(f"- `{cid}`")
    return "\n".join(lines)


def format_no_baseline_markdown() -> str:
    """baseline.json 이 없을 때의 placeholder 리포트."""
    return (
        "## ℹ️ Eval Report (no baseline)\n\n"
        "`eval/baseline.json` 이 없어 회귀 비교를 건너뜁니다. "
        "Telegram `/eval baseline` 명령으로 기준선을 한 번 기록 후 "
        "다시 워크플로우를 돌리면 비교가 활성화됩니다."
    )


def _load_summary(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _resolve_current_path(args: argparse.Namespace) -> Path:
    if args.current:
        return Path(args.current)
    if args.run_dir:
        return Path(args.run_dir) / "_summary.json"
    raise SystemExit("error: --run-dir 또는 --current 중 하나 필수")


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="eval_compare",
        description="Compare an eval run to baseline; output markdown report.",
    )
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument(
        "--run-dir",
        help="회차 디렉토리 (안에서 _summary.json 을 읽음)",
    )
    g.add_argument(
        "--current",
        help="현재 summary JSON 직접 경로 (run-dir 와 택1)",
    )
    p.add_argument(
        "--baseline",
        default="eval/baseline.json",
        help="기준선 JSON 경로 (기본 eval/baseline.json)",
    )
    p.add_argument(
        "--threshold-pp",
        type=float,
        default=10.0,
        help="통과율 회귀 임계값 (percentage points, 기본 10)",
    )
    p.add_argument(
        "--output",
        help="결과 markdown 저장 경로 (없으면 stdout)",
    )
    args = p.parse_args(argv if argv is not None else sys.argv[1:])

    current_path = _resolve_current_path(args)
    if not current_path.is_file():
        print(
            f"error: current summary not found: {current_path}",
            file=sys.stderr,
        )
        return 2

    baseline_path = Path(args.baseline)
    if not baseline_path.is_file():
        md = format_no_baseline_markdown()
        if args.output:
            Path(args.output).write_text(md, encoding="utf-8")
        else:
            print(md)
        return 0  # 첫 회차/baseline 없음은 워크플로우 실패가 아님

    current = _load_summary(current_path)
    baseline = _load_summary(baseline_path)

    result = compare(current, baseline, args.threshold_pp)
    md = format_markdown(result)

    if args.output:
        Path(args.output).write_text(md, encoding="utf-8")
    else:
        print(md)

    return 1 if result["verdict"] == "regression" else 0


if __name__ == "__main__":
    raise SystemExit(main())
