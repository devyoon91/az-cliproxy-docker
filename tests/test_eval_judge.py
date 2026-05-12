"""eval/judge.py 검증.

LLM 호출은 FakeJudgeClient 로 모킹.  실제 Anthropic 호출 없이:
- 프롬프트 조립
- JSON 응답 파싱 (코드 펜스/preamble/malformed 케이스)
- 점수 클램프 / passed 임계값
- 클라이언트 오류 / 파싱 실패 분기
- judge_run_dir 의 디스크 IO (judge.json 작성 + trace.json judge_cost_usd 갱신)
- CLI 파싱
"""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from eval.judge import (
    DEFAULT_JUDGE_MODEL,
    DEFAULT_PASS_THRESHOLD,
    FakeJudgeClient,
    JudgeResponse,
    _compute_judge_cost,
    _parse_args,
    build_user_prompt,
    judge_run_dir,
    judge_trace,
    parse_judge_response,
)
from eval.schema import EvalCase


def _make_case(**overrides) -> EvalCase:
    defaults = dict(
        id="t1",
        task="요약해줘",
        expected_behaviors=["한국어로 답한다", "3줄 이내로 답한다"],
        judge_criteria="간결하고 정확해야 함",
        tags=["test"],
        max_turns=5,
        max_cost_usd=0.10,
        timeout_sec=60,
    )
    defaults.update(overrides)
    return EvalCase(**defaults)


def _make_trace_data(response: str = "응답입니다") -> dict:
    return {
        "case_id": "t1",
        "task": "요약해줘",
        "started_at": "2026-05-12T01:00:00+00:00",
        "ended_at": "2026-05-12T01:00:05+00:00",
        "duration_ms": 5000,
        "az_task_id": "task-1",
        "turns": 1,
        "tool_calls": [],
        "final_response": response,
        "input_tokens": 100,
        "output_tokens": 20,
        "cache_read_tokens": 0,
        "cache_creation_tokens": 0,
        "reasoning_tokens": 0,
        "cost_usd": 0.001,
        "guard_violations": [],
        "error": None,
        "judge_cost_usd": 0.0,
    }


def _good_judge_response(
    *, score: float = 0.9, failed: list | None = None
) -> JudgeResponse:
    payload = {
        "score": score,
        "reasoning": "거의 모든 기준 충족",
        "failed_behaviors": failed or [],
    }
    return JudgeResponse(
        content=json.dumps(payload, ensure_ascii=False),
        input_tokens=300,
        output_tokens=40,
    )


# ── 프롬프트 ────────────────────────────────────────────────────────────


def test_build_user_prompt_contains_all_sections():
    case = _make_case()
    prompt = build_user_prompt(case, "테스트 응답")
    assert "Task:\n요약해줘" in prompt
    assert "- 한국어로 답한다" in prompt
    assert "- 3줄 이내로 답한다" in prompt
    assert "Judge criteria:\n간결하고 정확해야 함" in prompt
    assert "AI response:\n테스트 응답" in prompt


def test_build_user_prompt_empty_response_uses_placeholder():
    case = _make_case()
    prompt = build_user_prompt(case, "")
    assert "AI response:\n(empty)" in prompt


def test_build_user_prompt_whitespace_only_response_uses_placeholder():
    case = _make_case()
    prompt = build_user_prompt(case, "   \n  ")
    assert "AI response:\n(empty)" in prompt


def test_build_user_prompt_empty_criteria_uses_placeholder():
    case = _make_case(judge_criteria="")
    prompt = build_user_prompt(case, "응답")
    assert "Judge criteria:\n(none)" in prompt


# ── JSON 파싱 ───────────────────────────────────────────────────────────


def test_parse_judge_response_plain_json():
    text = '{"score": 0.8, "reasoning": "ok", "failed_behaviors": []}'
    out = parse_judge_response(text)
    assert out["score"] == 0.8


def test_parse_judge_response_with_code_fence():
    text = """```json
{"score": 0.5, "reasoning": "절반", "failed_behaviors": ["x"]}
```"""
    out = parse_judge_response(text)
    assert out["score"] == 0.5
    assert out["failed_behaviors"] == ["x"]


def test_parse_judge_response_with_preamble_and_trailing():
    text = (
        "Here is my evaluation:\n"
        '{"score": 0.7, "reasoning": "good", "failed_behaviors": []}\n'
        "End of response."
    )
    out = parse_judge_response(text)
    assert out["score"] == 0.7


def test_parse_judge_response_malformed_raises():
    with pytest.raises(ValueError, match="no JSON object"):
        parse_judge_response("not even close to JSON")


def test_parse_judge_response_invalid_json_raises():
    """첫 `{` 와 마지막 `}` 사이가 valid JSON 이 아니면 json.JSONDecodeError."""
    with pytest.raises(json.JSONDecodeError):
        parse_judge_response("{score: bad}")


# ── 비용 계산 ───────────────────────────────────────────────────────────


def test_compute_judge_cost_basic():
    # 1000 input @ $0.80/1M + 100 output @ $4.00/1M
    cost = _compute_judge_cost(1000, 100)
    assert cost == round(1000 * 0.0000008 + 100 * 0.000004, 6)


def test_compute_judge_cost_zero():
    assert _compute_judge_cost(0, 0) == 0.0


def test_compute_judge_cost_negative_clamped():
    """음수 입력은 0 으로 클램프 — provider quirk 방어."""
    assert _compute_judge_cost(-100, -50) == 0.0


# ── judge_trace ──────────────────────────────────────────────────────────


def test_judge_trace_happy_path():
    case = _make_case()
    trace = _make_trace_data()
    client = FakeJudgeClient([_good_judge_response(score=0.9)])
    result = asyncio.run(judge_trace(case, trace, client))

    assert result.case_id == "t1"
    assert result.score == 0.9
    assert result.passed is True  # 0.9 >= 0.7
    assert result.reasoning == "거의 모든 기준 충족"
    assert result.failed_behaviors == []
    assert result.judge_model == DEFAULT_JUDGE_MODEL
    assert result.input_tokens == 300
    assert result.output_tokens == 40
    assert result.cost_usd > 0
    assert result.error is None


def test_judge_trace_below_threshold_marks_failed():
    case = _make_case()
    trace = _make_trace_data()
    client = FakeJudgeClient(
        [_good_judge_response(score=0.5, failed=["한국어로 답한다"])]
    )
    result = asyncio.run(judge_trace(case, trace, client))
    assert result.score == 0.5
    assert result.passed is False
    assert result.failed_behaviors == ["한국어로 답한다"]


def test_judge_trace_custom_threshold():
    case = _make_case()
    trace = _make_trace_data()
    client = FakeJudgeClient([_good_judge_response(score=0.5)])
    result = asyncio.run(
        judge_trace(case, trace, client, pass_threshold=0.4)
    )
    assert result.passed is True  # 0.5 >= 0.4


def test_judge_trace_score_clamped_high():
    """모델이 1.2 같이 반환해도 1.0 으로 캡."""
    case = _make_case()
    trace = _make_trace_data()
    client = FakeJudgeClient([_good_judge_response(score=1.5)])
    result = asyncio.run(judge_trace(case, trace, client))
    assert result.score == 1.0


def test_judge_trace_score_clamped_low():
    case = _make_case()
    trace = _make_trace_data()
    client = FakeJudgeClient([_good_judge_response(score=-0.3)])
    result = asyncio.run(judge_trace(case, trace, client))
    assert result.score == 0.0


def test_judge_trace_non_numeric_score_falls_back_to_zero():
    case = _make_case()
    trace = _make_trace_data()
    client = FakeJudgeClient(
        [
            JudgeResponse(
                content='{"score": "high", "reasoning": "x", "failed_behaviors": []}',
                input_tokens=100,
                output_tokens=10,
            )
        ]
    )
    result = asyncio.run(judge_trace(case, trace, client))
    assert result.score == 0.0
    assert result.passed is False
    assert result.error is None  # 파싱은 성공, 값만 fallback


def test_judge_trace_client_failure_recorded_as_error():
    case = _make_case()
    trace = _make_trace_data()
    client = FakeJudgeClient([], error_on_call=True)
    result = asyncio.run(judge_trace(case, trace, client))
    assert result.error and "judge call failed" in result.error
    assert result.score == 0.0
    assert result.passed is False
    assert result.cost_usd == 0.0


def test_judge_trace_unparseable_response_recorded_as_error():
    case = _make_case()
    trace = _make_trace_data()
    client = FakeJudgeClient(
        [
            JudgeResponse(
                content="이 응답은 JSON 이 아닙니다",
                input_tokens=200,
                output_tokens=20,
            )
        ]
    )
    result = asyncio.run(judge_trace(case, trace, client))
    assert result.error and "not parseable" in result.error
    # 호출 자체는 성공했으므로 토큰/비용은 기록.
    assert result.input_tokens == 200
    assert result.cost_usd > 0


def test_judge_trace_failed_behaviors_must_be_list():
    """모델이 failed_behaviors 를 str 로 반환하면 빈 리스트로 정규화."""
    case = _make_case()
    trace = _make_trace_data()
    client = FakeJudgeClient(
        [
            JudgeResponse(
                content='{"score": 0.6, "reasoning": "x", "failed_behaviors": "한국어로 답한다"}',
                input_tokens=100,
                output_tokens=10,
            )
        ]
    )
    result = asyncio.run(judge_trace(case, trace, client))
    assert result.failed_behaviors == []


def test_judge_trace_uses_custom_model():
    case = _make_case()
    trace = _make_trace_data()
    client = FakeJudgeClient([_good_judge_response()])
    result = asyncio.run(
        judge_trace(case, trace, client, model="claude-opus-4-5")
    )
    assert result.judge_model == "claude-opus-4-5"
    # 클라이언트에도 같은 model 이 전달됐는지 확인.
    assert client.received_prompts[-1][2] == "claude-opus-4-5"


def test_judge_trace_passes_final_response_into_prompt():
    case = _make_case()
    trace = _make_trace_data(response="구체적인 응답 본문")
    client = FakeJudgeClient([_good_judge_response()])
    asyncio.run(judge_trace(case, trace, client))
    _, user_prompt, _ = client.received_prompts[-1]
    assert "구체적인 응답 본문" in user_prompt


# ── judge_run_dir ───────────────────────────────────────────────────────


def _setup_run_dir(tmp_path: Path) -> tuple[Path, Path]:
    """run_dir + cases_dir 준비.  trace 2개 + case YAML 2개."""
    cases_dir = tmp_path / "cases"
    cases_dir.mkdir()
    (cases_dir / "alpha.yaml").write_text(
        "task: t1\nexpected_behaviors: [b1]\n", encoding="utf-8"
    )
    (cases_dir / "beta.yaml").write_text(
        "task: t2\nexpected_behaviors: [b2]\n", encoding="utf-8"
    )

    run_dir = tmp_path / "run"
    run_dir.mkdir()
    (run_dir / "alpha.json").write_text(
        json.dumps(
            {**_make_trace_data(), "case_id": "alpha"}, ensure_ascii=False
        ),
        encoding="utf-8",
    )
    (run_dir / "beta.json").write_text(
        json.dumps(
            {**_make_trace_data(), "case_id": "beta"}, ensure_ascii=False
        ),
        encoding="utf-8",
    )
    # 잡파일 — 무시되어야 한다.
    (run_dir / "_summary.json").write_text(
        '{"x": 1}', encoding="utf-8"
    )
    return run_dir, cases_dir


def test_judge_run_dir_writes_judge_json_and_updates_trace(
    tmp_path: Path,
):
    run_dir, cases_dir = _setup_run_dir(tmp_path)
    client = FakeJudgeClient(
        [_good_judge_response(score=0.85), _good_judge_response(score=0.4)]
    )

    results = asyncio.run(
        judge_run_dir(run_dir, cases_dir, client)
    )
    assert len(results) == 2
    assert {r.case_id for r in results} == {"alpha", "beta"}

    # .judge.json 파일이 생성됐는가.
    a_judge = json.loads(
        (run_dir / "alpha.judge.json").read_text(encoding="utf-8")
    )
    assert a_judge["score"] == 0.85
    assert a_judge["passed"] is True

    b_judge = json.loads(
        (run_dir / "beta.judge.json").read_text(encoding="utf-8")
    )
    assert b_judge["score"] == 0.4
    assert b_judge["passed"] is False

    # trace JSON 에 judge_cost_usd 가 채워졌는가.
    a_trace = json.loads(
        (run_dir / "alpha.json").read_text(encoding="utf-8")
    )
    assert a_trace["judge_cost_usd"] > 0


def test_judge_run_dir_skips_traces_with_missing_case_yaml(
    tmp_path: Path,
):
    run_dir, cases_dir = _setup_run_dir(tmp_path)
    # case yaml 하나 삭제.
    (cases_dir / "alpha.yaml").unlink()
    client = FakeJudgeClient([_good_judge_response()])

    results = asyncio.run(
        judge_run_dir(run_dir, cases_dir, client)
    )
    # alpha 는 건너뛰고 beta 만 채점.
    assert len(results) == 1
    assert results[0].case_id == "beta"


def test_judge_run_dir_skips_summary_and_judge_files(tmp_path: Path):
    """_summary.json 과 이미 존재하는 .judge.json 은 트레이스로 오해하지 않는다."""
    run_dir, cases_dir = _setup_run_dir(tmp_path)
    # 미리 alpha.judge.json 을 심어두고, 한 번 더 돌려도 무한 루프 안 된다.
    (run_dir / "alpha.judge.json").write_text(
        '{"case_id": "alpha", "score": 0.0, "passed": false}',
        encoding="utf-8",
    )
    client = FakeJudgeClient([_good_judge_response()])
    results = asyncio.run(
        judge_run_dir(run_dir, cases_dir, client)
    )
    case_ids = [r.case_id for r in results]
    # _summary 와 alpha.judge 는 트레이스로 인식되지 않아야 한다.
    assert "_summary" not in case_ids
    # alpha 의 .judge.json 파일이 두 번째 트레이스로 picked-up 안 되어야 한다.
    # (즉 결과는 alpha, beta 둘 다 — alpha.json 은 여전히 정상 트레이스)
    assert sorted(case_ids) == ["alpha", "beta"]


def test_judge_run_dir_handles_judge_error_without_updating_trace_cost(
    tmp_path: Path,
):
    """판정 오류가 난 케이스는 trace.json 에 judge_cost_usd 를 쓰지 않는다."""
    run_dir, cases_dir = _setup_run_dir(tmp_path)
    client = FakeJudgeClient([], error_on_call=True)
    results = asyncio.run(
        judge_run_dir(run_dir, cases_dir, client)
    )
    for r in results:
        assert r.error
    a_trace = json.loads(
        (run_dir / "alpha.json").read_text(encoding="utf-8")
    )
    # 변경되지 않았어야 함.
    assert a_trace["judge_cost_usd"] == 0.0


# ── CLI ─────────────────────────────────────────────────────────────────


def test_parse_args_minimal():
    ns = _parse_args(["--run-dir", "some/path"])
    assert ns.run_dir == "some/path"
    assert ns.cases_dir == "eval/cases"
    assert ns.model == DEFAULT_JUDGE_MODEL
    assert ns.pass_threshold == DEFAULT_PASS_THRESHOLD


def test_parse_args_overrides():
    ns = _parse_args(
        [
            "--run-dir",
            "x",
            "--cases-dir",
            "y",
            "--model",
            "claude-opus-4-5",
            "--pass-threshold",
            "0.5",
        ]
    )
    assert ns.cases_dir == "y"
    assert ns.model == "claude-opus-4-5"
    assert ns.pass_threshold == 0.5


def test_parse_args_run_dir_required():
    with pytest.raises(SystemExit):
        _parse_args([])


# ── FakeJudgeClient 자체 ────────────────────────────────────────────────


def test_fake_judge_client_replays_last_when_queue_empty():
    only = _good_judge_response()
    client = FakeJudgeClient([only])
    r1 = asyncio.run(client.score(system="s", user="u1", model="m"))
    r2 = asyncio.run(client.score(system="s", user="u2", model="m"))
    assert r1 is only and r2 is only


def test_fake_judge_client_requires_responses_or_error_flag():
    with pytest.raises(ValueError):
        FakeJudgeClient([])
    # error_on_call=True 이면 빈 큐도 OK.
    FakeJudgeClient([], error_on_call=True)
