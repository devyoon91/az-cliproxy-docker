"""Recall skills whose trigger patterns match the user's current message.

Vendored from upstream agent-zero v1.13 with two local changes:

1. Description truncate ceiling raised 220 → 400. Korean descriptions
   carry less information per char (no whitespace tokens, multibyte
   glyphs), so 220 was clipping guard phrases like "이 스킬을 browser/
   search_engine 보다 우선 사용하라" before they reached the prompt.

2. Korean-aware match augmenter. Upstream `search_skills()` whitespace-
   splits the query and drops tokens shorter than 3 chars (unless they
   contain a digit). For Korean queries that's catastrophic — "공시"
   (2 chars) is exactly the kind of keyword that should fire k-dart,
   but it never makes it past the filter. Worse, the upstream scorer
   only checks `term in trigger` (does my query word appear inside any
   trigger phrase?) and not the natural reverse `trigger in query`
   (does any trigger phrase appear inside the user's query?). So even
   if "공시" survived filtering, the only matches would come from
   substring-in-trigger semantics, missing the obvious case where the
   user wrote the trigger phrase verbatim.

   Fix: after the upstream call, run our own `trigger in query` pass
   over `list_skills()` and merge any extra matches in. Upstream hits
   keep their position; ko-augmented hits fill the rest of the top-6.

Mounted into the container at the same upstream path so it overrides
the default extension. See docker-compose.yml for the bind mount.
"""
from agent import LoopData
from helpers import skills as skills_helper
from helpers.extension import Extension


def _augment_matches_korean(query: str, agent, base_matches):
    """Score skills by `trigger in query` (reverse of upstream's
    `term in trigger`) so Korean phrases like "최근 공시 보여줘" match
    a "공시" trigger. Returns a merged list — base_matches first
    (preserve upstream ranking), then ko-scored additions deduped.
    """
    q = (query or "").lower().strip()
    if not q:
        return base_matches

    try:
        candidates = skills_helper.list_skills(agent)
    except Exception:
        return base_matches

    seen = {m.name for m in base_matches}
    scored: list[tuple[int, object]] = []
    for s in candidates:
        if s.name in seen:
            continue
        score = 0
        for trigger in s.triggers or []:
            t = (trigger or "").strip().lower()
            # Floor at len 2 — "공시", "배당" etc. are valid signals in CJK.
            # Skip ultra-short triggers (1 char) to avoid runaway false positives.
            if len(t) >= 2 and t in q:
                # Specificity weight: longer phrases ("지분 공시") beat shorter
                # generic ones ("공시") so a more precise match ranks higher.
                score += max(len(t), 4)
        for tag in s.tags or []:
            t = (tag or "").lower()
            if len(t) >= 3 and t in q:
                score += 2
        name = (s.name or "").lower()
        if len(name) >= 3 and name in q:
            score += 6
        if score > 0:
            scored.append((score, s))

    scored.sort(key=lambda pair: -pair[0])
    extras = [s for _, s in scored]

    merged = list(base_matches) + extras
    return merged[:6]


class RecallRelevantSkills(Extension):
    async def execute(self, loop_data: LoopData = LoopData(), **kwargs):
        if not self.agent or loop_data.iteration != 0:
            return

        user_instruction = (
            loop_data.user_message.output_text() if loop_data.user_message else ""
        ).strip()
        if len(user_instruction) < 8:
            return

        upstream = skills_helper.search_skills(
            user_instruction,
            limit=6,
            agent=self.agent,
        )
        matches = _augment_matches_korean(user_instruction, self.agent, upstream)

        # One-line trace for /a0 logs so we can audit recall hits without
        # tailing turn-by-turn. Cheap and useful — keep it on.
        try:
            print(
                f"[RecallSkills] q={user_instruction[:80]!r} "
                f"upstream={[m.name for m in upstream]} "
                f"final={[m.name for m in matches]}",
                flush=True,
            )
        except Exception:
            pass

        if not matches:
            return

        lines: list[str] = []
        for skill in matches:
            name = skill.name.strip().replace("\n", " ")[:100]
            desc = (skill.description or "").replace("\n", " ").strip()
            if len(desc) > 400:
                desc = desc[:400].rstrip() + "…"
            lines.append(f"- {name}: {desc}")

        if not lines:
            return

        loop_data.extras_temporary["relevant_skills"] = self.agent.read_prompt(
            "agent.system.skills.relevant.md",
            skills="\n".join(lines),
        )
