"""Pin _63_recall_relevant_skills._augment_matches_korean (PR #66).

The bug it fixed: upstream `helpers.skills.search_skills()` whitespace-tokenizes
the query and drops tokens shorter than 3 chars. For Korean, that means
`"공시"`, `"배당"`, `"증자"` — exactly the high-signal keywords — get filtered
before scoring. Worse, the scorer only checks `term in trigger` and never the
natural reverse direction `trigger in query`.

The augmenter does its own `trigger in query` pass over `list_skills()`, with
length-weighted scoring so multi-word triggers ("지분 공시") rank above generic
ones ("공시"), and floors trigger length at 2 chars to keep CJK keywords.

Pinning:
1. Augmenter rescues k-dart for "삼성전자 최근 공시 10건 보여줘" when upstream returns [].
2. Already-matched skills aren't double-counted.
3. Length weighting puts a longer trigger ahead of a shorter one.
4. Triggers shorter than 2 chars don't contribute (false-positive guard).
"""
from __future__ import annotations

import importlib.util
import sys
from dataclasses import dataclass, field
from pathlib import Path

_EXT_PATH = (
    Path(__file__).resolve().parent.parent
    / "agent-zero" / "extensions" / "python" / "message_loop_prompts_after"
    / "_63_recall_relevant_skills.py"
)


@dataclass
class FakeSkill:
    """Just enough of helpers.skills.Skill for the augmenter."""
    name: str
    description: str = ""
    triggers: list = field(default_factory=list)
    tags: list = field(default_factory=list)


def _load_recall_module(canned_skills):
    """Load the extension with `helpers.skills.list_skills` returning the
    given list. Module is reloaded per test so the canned list takes effect."""
    sys.modules.pop("_63_recall_relevant_skills", None)

    helpers_skills = sys.modules.get("helpers.skills")
    assert helpers_skills is not None, "stubs.py should have installed helpers.skills"
    helpers_skills.list_skills = lambda agent=None: list(canned_skills)

    spec = importlib.util.spec_from_file_location("_63_recall_relevant_skills", _EXT_PATH)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


# ── canonical skill set used across tests ───────────────────────────


def _vendored_skills():
    return [
        FakeSkill(
            name="k-dart",
            description="DART API for Korean public company disclosures",
            triggers=["공시", "전자공시", "DART", "재무제표", "지분 공시"],
            tags=["finance", "dart"],
        ),
        FakeSkill(
            name="korean-law-search",
            description="Korean law / statute lookup",
            triggers=["법령", "법률", "조문", "근로기준법"],
            tags=["legal", "korean-law"],
        ),
        FakeSkill(
            name="hwp",
            description="HWP file converter",
            triggers=["hwp", "한컴"],
            tags=["document"],
        ),
    ]


# ── tests ───────────────────────────────────────────────────────────


def test_rescues_kdart_when_upstream_misses():
    """The exact case from PR #66's diagnosis."""
    mod = _load_recall_module(_vendored_skills())
    out = mod._augment_matches_korean(
        "삼성전자 최근 공시 10건 보여줘",
        agent=None,
        base_matches=[],
    )
    assert [s.name for s in out] == ["k-dart"]


def test_no_double_count_when_upstream_already_matched():
    """If upstream already returned a skill, augmenter must not re-add it."""
    skills = _vendored_skills()
    kdart = next(s for s in skills if s.name == "k-dart")
    mod = _load_recall_module(skills)
    out = mod._augment_matches_korean(
        "DART 공시 보여줘",
        agent=None,
        base_matches=[kdart],
    )
    names = [s.name for s in out]
    assert names.count("k-dart") == 1


def test_longer_trigger_ranks_above_generic():
    """'지분 공시' (4 chars) should outrank '공시' (2 chars) when both fire.

    We construct two skills both having a relevant trigger, where one's
    trigger is more specific. The one with the more specific trigger should
    appear first in the augmented output."""
    specific = FakeSkill(name="dart-equity", triggers=["지분 공시", "공시"])
    generic = FakeSkill(name="generic-news", triggers=["공시"])
    mod = _load_recall_module([generic, specific])
    out = mod._augment_matches_korean(
        "오늘 지분 공시 알려줘",
        agent=None,
        base_matches=[],
    )
    names = [s.name for s in out]
    assert names[0] == "dart-equity", f"specificity weighting failed: {names}"


def test_one_char_triggers_ignored():
    """Floor at len >= 2 to avoid runaway 1-char matches."""
    noisy = FakeSkill(name="noise", triggers=["a", "b", "오"])  # 1-char Korean too
    mod = _load_recall_module([noisy])
    out = mod._augment_matches_korean(
        "오늘 a b 어쩌구",
        agent=None,
        base_matches=[],
    )
    assert out == []


def test_returns_base_unchanged_when_query_empty():
    skills = _vendored_skills()
    mod = _load_recall_module(skills)
    out = mod._augment_matches_korean("", agent=None, base_matches=skills[:1])
    assert out == skills[:1]
