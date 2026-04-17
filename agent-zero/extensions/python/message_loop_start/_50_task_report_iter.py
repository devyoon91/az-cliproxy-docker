"""
Update iteration count on each message_loop_start tick.
See agent-zero/lib/task_report.py and issue #1.
"""

from helpers.extension import Extension
from helpers.task_report import record_iteration


class TaskReportIter(Extension):
    async def execute(self, loop_data=None, **kwargs):
        if loop_data is None:
            return
        record_iteration(self.agent, getattr(loop_data, "iteration", 0))
