"""Recall skills whose trigger patterns match the user's current message.

Vendored from upstream agent-zero v1.13 with one local change: the
description truncate ceiling is raised from 220 → 400 chars.

Why: Korean-domain skill descriptions ship lower information density
per char than English (no whitespace tokens, multibyte glyphs), so 220
clips guard phrases like "이 스킬을 browser/search_engine 보다 우선
사용하라" before they reach the prompt. 400 keeps the guard intact
without bloating the system prompt — typical descriptions still come
in well under that.

Mounted into the container at the same upstream path so it overrides
the default extension. See docker-compose.yml for the bind mount.
"""
from agent import LoopData
from helpers.extension import Extension
from helpers import skills as skills_helper


class RecallRelevantSkills(Extension):
    async def execute(self, loop_data: LoopData = LoopData(), **kwargs):
        if not self.agent or loop_data.iteration != 0:
            return

        user_instruction = (
            loop_data.user_message.output_text() if loop_data.user_message else ""
        ).strip()
        if len(user_instruction) < 8:
            return

        matches = skills_helper.search_skills(
            user_instruction,
            limit=6,
            agent=self.agent,
        )
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
