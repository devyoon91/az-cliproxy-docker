"""Eval trace — 한 케이스 실행 결과의 표준 직렬화.

AZ 의 task_report JSON 을 1차 소스로 삼아 우리 트레이스 포맷으로 정규화.
필드는 가능한 한 task_report 의 키와 맞춰 다운스트림(judge, dashboard)이
양쪽 모두를 같은 키로 다룰 수 있게 한다.

task_report 가 사용할 수 없는 경우(스텁/오류) 호출자가 빈 trace 또는
`error` 가 채워진 trace 를 만들 수 있도록 모든 토큰 필드는 0 기본값.
"""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ToolCall:
    """task_report 의 `tool_calls[]` 한 항목을 정규화한 형태."""

    name: str
    args_preview: str
    duration_ms: int


@dataclass
class Trace:
    """단일 eval 케이스 실행 결과.

    `from_task_report` 가 표준 컨버터.  guard_violations / error 는
    runner 가 정책 위반(턴 초과, 비용 초과, 타임아웃)을 기록할 자리.
    """

    case_id: str
    task: str
    started_at: str
    ended_at: str
    duration_ms: int

    az_task_id: str | None
    turns: int
    tool_calls: list[ToolCall]
    final_response: str

    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_creation_tokens: int
    reasoning_tokens: int
    cost_usd: float

    guard_violations: list[str] = field(default_factory=list)
    error: str | None = None

    # judge (#112) 가 채우는 별도 비용 — runner 자체에서는 항상 0.
    # 트레이스 단위 총비용은 `cost_usd + judge_cost_usd`.
    judge_cost_usd: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        """asdict 결과를 그대로 JSON 직렬화에 쓰기 위한 보조."""
        return asdict(self)

    @classmethod
    def from_task_report(
        cls,
        *,
        case_id: str,
        task: str,
        started_at: str,
        ended_at: str,
        report: dict[str, Any],
    ) -> Trace:
        """AZ task_report JSON 을 받아 Trace 로 변환.

        `totals` 의 토큰/비용을 우선 사용한다.  `final_response` 는
        task_report 가 채우는 최상위 필드를 먼저 보고, 없으면 마지막
        `response` 툴의 `args_preview` 를 폴백으로.
        """
        totals = report.get("totals", {}) or {}

        tool_calls: list[ToolCall] = []
        for tc in report.get("tool_calls", []) or []:
            duration_ms = tc.get("duration_ms")
            if duration_ms is None:
                duration_ms = int((tc.get("duration_sec", 0) or 0) * 1000)
            tool_calls.append(
                ToolCall(
                    name=str(tc.get("name", "")),
                    args_preview=str(tc.get("args_preview", "")),
                    duration_ms=int(duration_ms or 0),
                )
            )

        final_response = report.get("final_response") or ""
        if not final_response:
            # task_report.py 가 final_response 를 못 잡은 경우 — response
            # 툴의 args_preview 가 가장 가까운 폴백 (truncated 일 수 있음).
            for tc in reversed(report.get("tool_calls", []) or []):
                if tc.get("name") == "response":
                    final_response = tc.get("args_preview", "")
                    break

        duration_ms = int((report.get("elapsed_sec", 0) or 0) * 1000)
        if duration_ms == 0:
            duration_ms = _diff_ms(started_at, ended_at)

        return cls(
            case_id=case_id,
            task=task,
            started_at=started_at,
            ended_at=ended_at,
            duration_ms=duration_ms,
            az_task_id=report.get("task_id"),
            turns=int(report.get("iterations", 0) or 0),
            tool_calls=tool_calls,
            final_response=str(final_response or ""),
            input_tokens=int(totals.get("input_tokens", 0) or 0),
            output_tokens=int(totals.get("output_tokens", 0) or 0),
            cache_read_tokens=int(totals.get("cache_read_tokens", 0) or 0),
            cache_creation_tokens=int(
                totals.get("cache_creation_tokens", 0) or 0
            ),
            reasoning_tokens=int(totals.get("reasoning_tokens", 0) or 0),
            cost_usd=float(totals.get("cost_usd", 0.0) or 0.0),
        )


def _diff_ms(start_iso: str, end_iso: str) -> int:
    """ISO 두 시각의 차이(ms).  파싱 실패 시 0."""
    try:
        s = datetime.fromisoformat(start_iso.replace("Z", "+00:00"))
        e = datetime.fromisoformat(end_iso.replace("Z", "+00:00"))
        return max(0, int((e - s).total_seconds() * 1000))
    except (ValueError, AttributeError):
        return 0


def now_iso() -> str:
    """현재 UTC 시각을 ISO8601 로.  trace 의 started_at/ended_at 표준."""
    return datetime.now(timezone.utc).isoformat()  # noqa: UP017


def make_run_dir(base: Path | str, *, started_at: datetime | None = None) -> Path:
    """`<base>/<YYYYmmdd-HHMMSS>/` 신규 디렉토리 생성.  runner 가 한 회차의
    모든 케이스를 같은 디렉토리에 모으는 데 사용."""
    started_at = started_at or datetime.now(timezone.utc)  # noqa: UP017
    stamp = started_at.strftime("%Y%m%d-%H%M%S")
    d = Path(base) / stamp
    d.mkdir(parents=True, exist_ok=True)
    return d


def save_trace(trace: Trace, run_dir: Path | str) -> Path:
    """Trace 를 `<run_dir>/<case_id>.json` 으로 직렬화."""
    d = Path(run_dir)
    d.mkdir(parents=True, exist_ok=True)
    path = d / f"{trace.case_id}.json"
    path.write_text(
        json.dumps(trace.to_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return path
