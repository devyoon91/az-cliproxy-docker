"""
Startup sweep — mark leftover "pending" task JSONs as "orphaned".

Any JSON still in `ended_reason="pending"` at AZ boot time means either:
  - the previous container died mid-run (crash, docker-compose restart), or
  - the user cancelled/killed the task (monologue_end is skipped because
    asyncio.CancelledError propagates past the @extensible end-hook).

Promoting them to "orphaned" at startup makes downstream reporting
unambiguous — a future daily-summary consumer can distinguish completed
runs from unfinished ones without having to guess from file mtime.

Runs once per agent_init cycle; sweep is idempotent.

See agent-zero/lib/task_report.py and issue #1.
"""

from helpers.extension import Extension
from helpers.task_report import sweep_orphans


_swept_once = False


class TaskReportSweep(Extension):
    def execute(self, **kwargs):
        global _swept_once
        if _swept_once:
            return
        try:
            sweep_orphans()
        finally:
            _swept_once = True
