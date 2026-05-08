"""Pytest configuration — stubs Agent-Zero internals so we can import
the modules under test without spinning up the container.

Why this is needed:
- `agent-zero/lib/pricing.py` is pure Python, no agent-zero deps. Loads fine.
- `_91_chunk_usage_probe.py` imports `helpers.task_report` (ContextVar),
  `helpers.extension` (Extension base class), and `litellm`. All three
  are agent-zero-internal or heavy externals — stub them.
- `_63_recall_relevant_skills.py` imports `helpers.skills`, `helpers.extension`,
  `agent.LoopData`. Same story.
- `chat_pdf_export/api/export_pdf.py` imports `agent.AgentContext`,
  `helpers.api.ApiHandler`. Stub.
- `bot.py` is too entangled — defer until the #79 refactor.

The stubs live in stubs.py and are installed into sys.modules HERE, so
every test module sees the same fake-import landscape from the moment
collection starts. Don't move this into individual tests — pytest may
import test files in any order, and once a real `helpers.task_report`
is in sys.modules, you can't put a stub back in cleanly.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Make `tests.stubs` importable even though tests/__init__.py is empty.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from tests import stubs  # noqa: E402

stubs.install_all()
