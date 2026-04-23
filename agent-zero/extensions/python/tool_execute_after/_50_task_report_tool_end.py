"""
Record tool call duration, result size, and break_loop after tool.execute().
Also triggers a periodic save so cancel/kill paths leave up-to-date data.
See agent-zero/lib/task_report.py and issue #1.
"""

from helpers.extension import Extension
from helpers.task_report import tool_end, save_task


class TaskReportToolEnd(Extension):
    async def execute(self, tool_name="", response=None, **kwargs):
        tool_end(self.agent, tool_name, response)
        # Persist after each tool call — tool executions are the longest
        # running units inside a monologue iteration and the most common
        # point where a user hits stop.
        save_task(self.agent)
