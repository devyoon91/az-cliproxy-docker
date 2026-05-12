"""LLM-as-judge — case + trace → score/passed/reasoning.

util 모델 (기본 Haiku 4.5) 에 expected_behaviors + judge_criteria + 응답을
주고 JSON 으로 점수를 받는다.  점수 / 통과 여부 / 실패한 행동 목록 / 한 줄
근거를 `eval/runs/<timestamp>/<case_id>.judge.json` 에 기록하고, 트레이스
파일에는 judge 호출 비용을 추가한다.

설계 분리:
- `JudgeClient` Protocol — runner 의 AZClient 와 같은 패턴.
  - `HTTPJudgeClient` 가 실제 Anthropic Messages API 호출.
  - `FakeJudgeClient` 가 캔드 응답으로 테스트.
- `parse_judge_response` 가 LLM 의 흔한 quirks (코드 펜스, preamble) 를 흡수.
- 비용 계산은 Haiku 4.5 고정 rate.  agent-zero/lib/pricing.py 의
  canonical 값과 일치하도록 두 곳에서 같이 관리.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Protocol

from .schema import EvalCase, load_case

logger = logging.getLogger(__name__)


# 기본 통과 임계값 — score >= 이 값이면 passed=True.
DEFAULT_PASS_THRESHOLD = 0.7

# 기본 judge 모델.  settings.example.json 의 util_model_name 과 일치.
DEFAULT_JUDGE_MODEL = "claude-haiku-4-5"

# Haiku 4.5 per-token rates (USD).  Anthropic 가격이 바뀌면 여기와
# agent-zero/lib/pricing.py 의 _ALIASES/테이블을 함께 업데이트.
# 캐싱은 judge 가 단일-호출이라 무시 (cache hit 자체가 안 일어남).
_HAIKU_INPUT_RATE = 0.0000008    # $0.80 / 1M
_HAIKU_OUTPUT_RATE = 0.000004    # $4.00 / 1M


# ── 데이터 클래스 ────────────────────────────────────────────────────────


@dataclass(frozen=True)
class JudgeResponse:
    """JudgeClient 가 반환하는 원시 응답 — runner 가 파싱 + 점수 산출."""

    content: str
    input_tokens: int
    output_tokens: int


@dataclass
class JudgeResult:
    """단일 케이스 채점 결과.  `.judge.json` 으로 직렬화."""

    case_id: str
    score: float
    passed: bool
    reasoning: str
    failed_behaviors: list[str]
    judge_model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    error: str | None = None


# ── 클라이언트 ──────────────────────────────────────────────────────────


class JudgeClient(Protocol):
    """eval/judge 가 의존하는 최소 인터페이스."""

    async def score(
        self, *, system: str, user: str, model: str
    ) -> JudgeResponse:
        ...


class HTTPJudgeClient:
    """Anthropic Messages API 호출용 실 클라이언트.

    anthropic SDK 는 옵션 의존 — `score()` 안에서 lazy import 한다.
    """

    def __init__(self, *, api_key: str | None = None) -> None:
        self.api_key = api_key or os.environ.get("ANTHROPIC_API_KEY", "")

    async def score(
        self, *, system: str, user: str, model: str
    ) -> JudgeResponse:
        import anthropic  # 지연 import

        if not self.api_key:
            raise RuntimeError("ANTHROPIC_API_KEY not set")

        client = anthropic.AsyncAnthropic(api_key=self.api_key)
        resp = await client.messages.create(
            model=model,
            max_tokens=512,
            system=system,
            messages=[{"role": "user", "content": user}],
            temperature=0,
        )
        text = ""
        for block in resp.content:
            if getattr(block, "type", "") == "text":
                text += getattr(block, "text", "")
        return JudgeResponse(
            content=text,
            input_tokens=int(getattr(resp.usage, "input_tokens", 0) or 0),
            output_tokens=int(getattr(resp.usage, "output_tokens", 0) or 0),
        )


class FakeJudgeClient:
    """캔드 응답을 순서대로 반환하는 테스트 더블.

    `error_on_call` 가 True 면 호출 시 RuntimeError 를 raise — 클라이언트
    오류 경로 (네트워크/인증 실패 등) 검증용.
    """

    def __init__(
        self,
        responses: list[JudgeResponse],
        *,
        error_on_call: bool = False,
    ) -> None:
        if not responses and not error_on_call:
            raise ValueError(
                "FakeJudgeClient requires at least one response "
                "or error_on_call=True"
            )
        self._responses = list(responses)
        self._idx = 0
        self._error_on_call = error_on_call
        self.received_prompts: list[tuple[str, str, str]] = []

    async def score(
        self, *, system: str, user: str, model: str
    ) -> JudgeResponse:
        self.received_prompts.append((system, user, model))
        if self._error_on_call:
            raise RuntimeError("simulated judge failure")
        if self._idx < len(self._responses):
            r = self._responses[self._idx]
            self._idx += 1
            return r
        return self._responses[-1]


# ── 프롬프트 ────────────────────────────────────────────────────────────


_SYSTEM_PROMPT = """You are an evaluator. Score how well an AI response satisfies a set of
expected behaviors for a given task.

Output ONLY valid JSON matching this schema. No preamble, no markdown fences.

{
  "score": <float between 0.0 and 1.0>,
  "reasoning": "<one sentence explaining the score>",
  "failed_behaviors": [<exact strings from the expected_behaviors list that were NOT met>]
}

Scoring:
- 1.0 = all expected behaviors met
- 0.0 = none met
- partial = (met / total), nudged by judge_criteria"""


def build_user_prompt(case: EvalCase, response_text: str) -> str:
    """case 와 관찰된 응답으로부터 judge user message 작성."""
    behaviors = "\n".join(f"- {b}" for b in case.expected_behaviors)
    criteria = case.judge_criteria.strip() or "(none)"
    response = (response_text or "").strip() or "(empty)"
    return (
        f"Task:\n{case.task}\n\n"
        f"Expected behaviors:\n{behaviors}\n\n"
        f"Judge criteria:\n{criteria}\n\n"
        f"AI response:\n{response}"
    )


def parse_judge_response(content: str) -> dict[str, Any]:
    """LLM 응답에서 JSON 객체 추출.

    흔한 quirks:
    - ` ```json … ``` ` 코드 펜스로 감싸기
    - "Here is the JSON: { … }" 같이 preamble
    - 마지막에 trailing 텍스트

    전략: 첫 `{` 부터 마지막 `}` 까지를 잘라 json.loads.  실패 시 ValueError.
    """
    text = content.strip()
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end < 0 or end < start:
        raise ValueError(f"no JSON object found in: {content[:200]}")
    snippet = text[start : end + 1]
    return json.loads(snippet)


def _compute_judge_cost(input_tokens: int, output_tokens: int) -> float:
    """Haiku 4.5 rate 기반 비용 (USD).  6자리 반올림."""
    return round(
        max(0, int(input_tokens)) * _HAIKU_INPUT_RATE
        + max(0, int(output_tokens)) * _HAIKU_OUTPUT_RATE,
        6,
    )


# ── 핵심 채점 ───────────────────────────────────────────────────────────


async def judge_trace(
    case: EvalCase,
    trace_data: dict[str, Any],
    client: JudgeClient,
    *,
    model: str = DEFAULT_JUDGE_MODEL,
    pass_threshold: float = DEFAULT_PASS_THRESHOLD,
) -> JudgeResult:
    """단일 트레이스 채점.  실패 경로(클라이언트 오류/파싱 실패) 모두
    JudgeResult 의 `error` 필드로 흡수해 호출자가 균일하게 처리하도록 한다."""
    final_response = trace_data.get("final_response", "") or ""
    user_prompt = build_user_prompt(case, final_response)

    try:
        resp = await client.score(
            system=_SYSTEM_PROMPT, user=user_prompt, model=model
        )
    except Exception as e:
        return JudgeResult(
            case_id=case.id,
            score=0.0,
            passed=False,
            reasoning="",
            failed_behaviors=[],
            judge_model=model,
            input_tokens=0,
            output_tokens=0,
            cost_usd=0.0,
            error=f"judge call failed: {e}",
        )

    cost = _compute_judge_cost(resp.input_tokens, resp.output_tokens)

    try:
        parsed = parse_judge_response(resp.content)
    except (ValueError, json.JSONDecodeError) as e:
        return JudgeResult(
            case_id=case.id,
            score=0.0,
            passed=False,
            reasoning="",
            failed_behaviors=[],
            judge_model=model,
            input_tokens=resp.input_tokens,
            output_tokens=resp.output_tokens,
            cost_usd=cost,
            error=f"judge response not parseable: {e}",
        )

    # 점수 범위 클램프 — 모델이 1.2 같이 내놓아도 1.0 으로 캡.
    raw_score = parsed.get("score", 0.0)
    try:
        score = float(raw_score)
    except (TypeError, ValueError):
        score = 0.0
    score = max(0.0, min(1.0, score))

    failed = parsed.get("failed_behaviors", []) or []
    if not isinstance(failed, list):
        failed = []
    failed = [str(x) for x in failed]

    return JudgeResult(
        case_id=case.id,
        score=score,
        passed=score >= pass_threshold,
        reasoning=str(parsed.get("reasoning", "")),
        failed_behaviors=failed,
        judge_model=model,
        input_tokens=resp.input_tokens,
        output_tokens=resp.output_tokens,
        cost_usd=cost,
    )


# ── 회차 디렉토리 단위 처리 ─────────────────────────────────────────────


async def judge_run_dir(
    run_dir: Path | str,
    cases_dir: Path | str,
    client: JudgeClient,
    *,
    model: str = DEFAULT_JUDGE_MODEL,
    pass_threshold: float = DEFAULT_PASS_THRESHOLD,
) -> list[JudgeResult]:
    """run_dir 안의 모든 트레이스를 채점.

    출력:
    - `<run_dir>/<case_id>.judge.json` (JudgeResult 직렬화)
    - `<run_dir>/<case_id>.json` 의 `judge_cost_usd` 필드 갱신 (error 가 없을 때만)
    """
    run_dir = Path(run_dir)
    cases_dir = Path(cases_dir)

    results: list[JudgeResult] = []
    for trace_path in sorted(run_dir.glob("*.json")):
        # _summary.json / .judge.json 은 건너뜀
        if trace_path.name.startswith("_"):
            continue
        if trace_path.name.endswith(".judge.json"):
            continue

        try:
            trace_data = json.loads(trace_path.read_text(encoding="utf-8"))
        except Exception as e:
            logger.warning(f"skip unreadable trace {trace_path.name}: {e}")
            continue

        case_id = trace_data.get("case_id")
        if not case_id:
            logger.warning(
                f"trace {trace_path.name} has no case_id — skip"
            )
            continue

        case_path = cases_dir / f"{case_id}.yaml"
        if not case_path.is_file():
            logger.warning(
                f"case yaml missing for {case_id} (expected {case_path})"
            )
            continue
        case = load_case(case_path)

        result = await judge_trace(
            case,
            trace_data,
            client,
            model=model,
            pass_threshold=pass_threshold,
        )
        results.append(result)

        # 1) JudgeResult 직렬화
        judge_path = run_dir / f"{case_id}.judge.json"
        judge_path.write_text(
            json.dumps(asdict(result), ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

        # 2) trace JSON 의 judge_cost_usd 갱신 — error 면 0 으로 두고
        # 그 외에는 실제 cost.  cost 가 있는 경우만 디스크에 다시 쓴다.
        if result.error is None and result.cost_usd > 0:
            trace_data["judge_cost_usd"] = result.cost_usd
            trace_path.write_text(
                json.dumps(trace_data, ensure_ascii=False, indent=2),
                encoding="utf-8",
            )

    return results


# ── CLI ─────────────────────────────────────────────────────────────────


def _parse_args(argv: list[str]) -> argparse.Namespace:
    p = argparse.ArgumentParser(
        prog="python -m eval.judge",
        description="Score eval traces with an LLM-as-judge.",
    )
    p.add_argument(
        "--run-dir",
        required=True,
        help="채점할 트레이스 JSON 들이 있는 디렉토리",
    )
    p.add_argument(
        "--cases-dir",
        default="eval/cases",
        help="case YAML 디렉토리 (기본 eval/cases)",
    )
    p.add_argument(
        "--model",
        default=DEFAULT_JUDGE_MODEL,
        help=f"judge 모델 (기본 {DEFAULT_JUDGE_MODEL})",
    )
    p.add_argument(
        "--pass-threshold",
        type=float,
        default=DEFAULT_PASS_THRESHOLD,
        help=f"passed=True 임계값 (기본 {DEFAULT_PASS_THRESHOLD})",
    )
    return p.parse_args(argv)


def _print_summary(results: list[JudgeResult]) -> None:
    passed = sum(1 for r in results if r.passed)
    failed = sum(1 for r in results if not r.passed and not r.error)
    errored = sum(1 for r in results if r.error)
    total_cost = sum(r.cost_usd for r in results)
    print(
        f"\n[judge] {passed}/{len(results)} passed, "
        f"{failed} failed, {errored} errored | "
        f"total cost ${total_cost:.4f}"
    )
    for r in results:
        if r.error:
            mark, detail = "ERR", r.error
        elif r.passed:
            mark, detail = "OK ", r.reasoning[:80]
        else:
            mark, detail = "FAIL", r.reasoning[:80]
        print(
            f"  [{mark}] {r.case_id:30s} score={r.score:.2f}  "
            f"cost=${r.cost_usd:.4f}  {detail}"
        )


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=os.environ.get("EVAL_LOG_LEVEL", "INFO"),
        format="%(asctime)s [%(levelname)s] %(message)s",
    )
    args = _parse_args(argv if argv is not None else sys.argv[1:])
    run_dir = Path(args.run_dir)
    cases_dir = Path(args.cases_dir)

    if not run_dir.is_dir():
        print(f"[judge] run_dir not found: {run_dir}", file=sys.stderr)
        return 2

    client = HTTPJudgeClient()
    results = asyncio.run(
        judge_run_dir(
            run_dir,
            cases_dir,
            client,
            model=args.model,
            pass_threshold=args.pass_threshold,
        )
    )
    _print_summary(results)

    if not results:
        return 1
    errored = sum(1 for r in results if r.error)
    failed = sum(1 for r in results if not r.passed and not r.error)
    return 0 if errored == 0 and failed == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
