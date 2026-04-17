"""
Stash tool call start time and args hash before tool.execute().
See agent-zero/lib/task_report.py and issue #1.
"""

from helpers.extension import Extension
from helpers.task_report import tool_start


class TaskReportToolStart(Extension):
    async def execute(self, tool_name="", tool_args=None, **kwargs):
        tool_start(self.agent, tool_name, tool_args or {})
