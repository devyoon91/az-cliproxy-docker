"""
Record an LLM call (model, tokens, cache reads) after each chat completion.
See agent-zero/lib/task_report.py and issue #1.
"""

from helpers.extension import Extension
from helpers.task_report import llm_call


class TaskReportLLM(Extension):
    async def execute(self, call_data=None, response=None, **kwargs):
        llm_call(self.agent, call_data, response)
