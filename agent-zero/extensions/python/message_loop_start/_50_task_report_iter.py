"""
Update iteration count + periodic save on each message_loop_start tick.

The save is idempotent and cheap — it re-serializes the in-memory report
to disk via atomic rename. This is the fallback path for cancel/kill runs
where monologue_end is skipped (see task_report.py for details).

See agent-zero/lib/task_report.py and issue #1.
"""

from helpers.extension import Extension
from helpers.task_report import record_iteration, save_task


class TaskReportIter(Extension):
    async def execute(self, loop_data=None, **kwargs):
        if loop_data is None:
            return
        record_iteration(self.agent, getattr(loop_data, "iteration", 0))
        # Periodic save — keeps the on-disk JSON current so cancel/kill paths
        # (where monologue_end is skipped) still leave usable data.
        save_task(self.agent)
