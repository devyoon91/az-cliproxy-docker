"""`_build_eval_stats(range_days)` — eval/runs/ 회차 summary JSON 들의 집계.

각 회차는 `eval/runs/<YYYYmmdd-HHMMSS>/_summary.json` 한 개를 떨군다
(#114 의 build_run_summary). 이 모듈은 그 파일들을 읽어 dashboard JS 가
바로 쓸 수 있는 JSON 으로 변환한다.

핵심 시계열 3개:
- pass_rate_trend: 회차별 통과율 (회귀 감지의 1차 신호)
- per_case_avg: 케이스별 평균 통과율·비용·지연
- duration_distribution: 케이스별 p50/p95/max 지연

dashboard/stats.py 와 같은 \"순수 dict 반환\" 패턴 — HTTP 핸들러는 이걸
json_response 로 감싸기만.
"""
from __future__ import annotations

import json
import logging
import os
from datetime import datetime, timedelta, timezone
from pathlib import Path

logger = logging.getLogger(__name__)


# 컨테이너 안에서의 eval/runs 위치.  docker-compose.yml 의
# ./eval:/app/eval 마운트 (#114) 가 전제.
EVAL_RUNS_DIR = Path(os.environ.get("EVAL_RUNS_DIR", "/app/eval/runs"))


# ── 시간 파싱 / 윈도우 ──────────────────────────────────────────────────


def _utc_now() -> datetime:
    return datetime.now(timezone.utc)  # noqa: UP017


def _parse_started_at(summary: dict) -> datetime | None:
    """summary['started_at'] ISO 문자열 → aware datetime.  파싱 실패 시 None."""
    s = summary.get("started_at")
    if not isinstance(s, str):
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)  # noqa: UP017
        return dt
    except ValueError:
        return None


def _load_run_summaries() -> list[dict]:
    """EVAL_RUNS_DIR 의 모든 _summary.json 을 읽어 started_at 오름차순 정렬.

    누락/오염 파일은 조용히 skip (디버그 로그).  디렉토리 자체가 없으면 빈 리스트.
    """
    if not EVAL_RUNS_DIR.is_dir():
        return []
    out: list[dict] = []
    for run_dir in EVAL_RUNS_DIR.iterdir():
        if not run_dir.is_dir():
            continue
        summary_path = run_dir / "_summary.json"
        if not summary_path.is_file():
            continue
        try:
            data = json.loads(summary_path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.debug(f"skip bad summary {summary_path}: {e}")
            continue
        out.append(data)
    out.sort(key=lambda s: s.get("started_at") or "")
    return out


def _filter_window(summaries: list[dict], range_days: int) -> list[dict]:
    """range_days 기준 윈도우 필터 — 현재 시각으로부터 N일 이내 회차만."""
    cutoff = _utc_now() - timedelta(days=range_days)
    out: list[dict] = []
    for s in summaries:
        ts = _parse_started_at(s)
        if ts is None:
            continue
        if ts >= cutoff:
            out.append(s)
    return out


# ── 집계 ────────────────────────────────────────────────────────────────


def _pass_rate(summary: dict) -> tuple[int, int]:
    """(passed, total).  total 은 채점된 + 가드 위반 + runner 에러 모두 포함."""
    total = int(summary.get("total", 0) or 0)
    passed = int(summary.get("passed_judges", 0) or 0)
    return passed, total


def _build_pass_rate_trend(summaries: list[dict]) -> list[dict]:
    out: list[dict] = []
    for s in summaries:
        passed, total = _pass_rate(s)
        rate = (passed / total) if total else 0.0
        out.append(
            {
                "started_at": s.get("started_at"),
                "passed": passed,
                "total": total,
                "pass_rate": round(rate, 4),
            }
        )
    return out


def _build_per_case_avg(summaries: list[dict]) -> list[dict]:
    """케이스별 평균 — 통과율, 실행 비용, 채점 비용, 지연."""
    agg: dict[str, dict] = {}
    for s in summaries:
        for c in s.get("cases", []) or []:
            cid = c.get("case_id")
            if not cid:
                continue
            bucket = agg.setdefault(
                cid,
                {
                    "runs": 0,
                    "pass_count": 0,
                    "sum_run_cost": 0.0,
                    "sum_judge_cost": 0.0,
                    "sum_duration_ms": 0,
                    "duration_samples": [],
                },
            )
            bucket["runs"] += 1
            if c.get("passed"):
                bucket["pass_count"] += 1
            bucket["sum_run_cost"] += float(c.get("run_cost_usd", 0.0) or 0.0)
            bucket["sum_judge_cost"] += float(
                c.get("judge_cost_usd", 0.0) or 0.0
            )
            d_ms = int(c.get("duration_ms", 0) or 0)
            bucket["sum_duration_ms"] += d_ms
            bucket["duration_samples"].append(d_ms)

    out: list[dict] = []
    for cid, b in agg.items():
        runs = b["runs"]
        out.append(
            {
                "case_id": cid,
                "runs": runs,
                "pass_count": b["pass_count"],
                "pass_rate": round(b["pass_count"] / runs, 4) if runs else 0.0,
                "avg_run_cost_usd": round(b["sum_run_cost"] / runs, 6)
                if runs
                else 0.0,
                "avg_judge_cost_usd": round(
                    b["sum_judge_cost"] / runs, 6
                )
                if runs
                else 0.0,
                "avg_duration_ms": int(b["sum_duration_ms"] / runs)
                if runs
                else 0,
            }
        )
    out.sort(key=lambda r: r["case_id"])
    return out


def _percentile(samples: list[int], pct: float) -> int:
    """샘플의 nearest-rank percentile.  빈 리스트면 0."""
    if not samples:
        return 0
    s = sorted(samples)
    k = max(0, min(len(s) - 1, int(round(pct / 100 * (len(s) - 1)))))
    return int(s[k])


def _build_duration_distribution(summaries: list[dict]) -> list[dict]:
    """케이스별 p50 / p95 / max 지연 (ms)."""
    samples: dict[str, list[int]] = {}
    for s in summaries:
        for c in s.get("cases", []) or []:
            cid = c.get("case_id")
            if not cid:
                continue
            d_ms = int(c.get("duration_ms", 0) or 0)
            samples.setdefault(cid, []).append(d_ms)

    out: list[dict] = []
    for cid, arr in samples.items():
        out.append(
            {
                "case_id": cid,
                "runs": len(arr),
                "p50_ms": _percentile(arr, 50),
                "p95_ms": _percentile(arr, 95),
                "max_ms": max(arr) if arr else 0,
            }
        )
    out.sort(key=lambda r: r["case_id"])
    return out


# ── 메인 ────────────────────────────────────────────────────────────────


def _build_eval_stats(range_days: int = 30) -> dict:
    """dashboard JS 가 fetch 해서 차트에 그릴 수 있는 모양으로 정리.

    Shape:
      {
        "now": "...",
        "range_days": 30,
        "total_runs": N,
        "latest": {...} | None,      # 가장 최근 회차의 summary 그대로
        "pass_rate_trend": [...],    # 회차별 통과율 (라인 차트)
        "per_case_avg": [...],       # 케이스별 평균 (바 차트)
        "duration_distribution": [...],  # 케이스별 p50/p95/max (분포 차트)
      }
    """
    all_summaries = _load_run_summaries()
    window = _filter_window(all_summaries, range_days)

    latest = window[-1] if window else (
        all_summaries[-1] if all_summaries else None
    )

    return {
        "now": _utc_now().isoformat(),
        "range_days": range_days,
        "total_runs": len(window),
        "latest": latest,
        "pass_rate_trend": _build_pass_rate_trend(window),
        "per_case_avg": _build_per_case_avg(window),
        "duration_distribution": _build_duration_distribution(window),
    }
