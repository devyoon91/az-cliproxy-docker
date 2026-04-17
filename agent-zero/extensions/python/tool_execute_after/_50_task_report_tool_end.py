"""
Record tool call duration, result size, and break_loop after tool.execute().
See agent-zero/lib/task_report.py and issue #1.
"""

from helpers.extension import Extension
from helpers.task_report import tool_end


class TaskReportToolEnd(Extension):
    async def execute(self, tool_name="", response=None, **kwargs):
        tool_end(self.agent, tool_name, response)
