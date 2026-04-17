"""
Finalize the task report and write JSON to /a0/logs/tasks/<task_id>.json.

NOTE: monologue_end is skipped on cancel/kill — a fallback via the implicit
@extensible hook (_functions/agent/Agent/monologue/end) is left for a
follow-up. See issue #1.
"""

from helpers.extension import Extension
from helpers.task_report import finish_task


class TaskReportFinish(Extension):
    async def execute(self, **kwargs):
        finish_task(self.agent)
