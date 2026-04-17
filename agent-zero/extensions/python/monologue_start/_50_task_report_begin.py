"""
Begin a task report on monologue_start.
See agent-zero/lib/task_report.py and issue #1.
"""

from helpers.extension import Extension
from helpers.task_report import begin_task


class TaskReportBegin(Extension):
    async def execute(self, **kwargs):
        begin_task(self.agent)
