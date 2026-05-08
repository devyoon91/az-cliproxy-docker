"""Fake `helpers.*`, `agent`, `litellm` modules for import-time satisfaction.

Each stub provides JUST the attribute the module-under-test reaches for —
no behavior. Tests that actually exercise that behavior pass real test
doubles into the function under test (see, e.g., test_skills_recall.py
where we feed a list of fake `Skill` objects directly to the augmenter).

Call `install_all()` once from conftest.py. Idempotent.
"""
from __future__ import annotations

import sys
import types
from contextvars import ContextVar


def _ensure_module(name: str) -> types.ModuleType:
    if name in sys.modules:
        return sys.modules[name]
    mod = types.ModuleType(name)
    sys.modules[name] = mod
    return mod


def _stub_helpers() -> None:
    """`helpers.task_report.last_stream_usage`, `helpers.extension.Extension`."""
    helpers = _ensure_module("helpers")

    task_report = _ensure_module("helpers.task_report")
    task_report.last_stream_usage = ContextVar("last_stream_usage", default=None)
    helpers.task_report = task_report  # type: ignore[attr-defined]

    extension = _ensure_module("helpers.extension")

    class _FakeExtension:
        def __init__(self, *args, **kwargs):
            self.agent = kwargs.get("agent")

    extension.Extension = _FakeExtension
    helpers.extension = extension  # type: ignore[attr-defined]

    skills = _ensure_module("helpers.skills")
    # Tests inject their own list_skills via monkeypatch; default is empty.
    skills.list_skills = lambda agent=None: []
    skills.search_skills = lambda query, limit=25, agent=None: []
    helpers.skills = skills  # type: ignore[attr-defined]

    api_mod = _ensure_module("helpers.api")

    class _FakeApiHandler:
        def __init__(self, *args, **kwargs):
            pass

    api_mod.ApiHandler = _FakeApiHandler
    api_mod.Input = dict
    api_mod.Output = object
    api_mod.Request = object
    helpers.api = api_mod  # type: ignore[attr-defined]


def _stub_agent() -> None:
    """`agent.AgentContext`, `agent.LoopData`."""
    agent = _ensure_module("agent")

    class _FakeAgentContext:
        _store: dict = {}

        def __init__(self, **kwargs):
            for k, v in kwargs.items():
                setattr(self, k, v)

        @classmethod
        def get(cls, ctxid):
            return cls._store.get(ctxid)

    class _FakeLoopData:
        def __init__(self, iteration=0, user_message=None):
            self.iteration = iteration
            self.user_message = user_message
            self.extras_temporary: dict = {}

    agent.AgentContext = _FakeAgentContext
    agent.LoopData = _FakeLoopData


def _stub_litellm() -> None:
    """Probe imports `litellm.acompletion` / `litellm.completion` for wrapping."""
    litellm = _ensure_module("litellm")
    litellm.acompletion = lambda *a, **k: None
    litellm.completion = lambda *a, **k: None


def _stub_flask() -> None:
    """chat_pdf_export imports `from flask import Response, jsonify`. We don't
    exercise the HTTP plumbing in unit tests — only `_build_chat_dict` etc."""
    flask = _ensure_module("flask")

    class _FakeResponse:
        def __init__(self, *args, **kwargs):
            self.args = args
            self.kwargs = kwargs

    flask.Response = _FakeResponse
    flask.jsonify = lambda *a, **k: ("jsonified", a, k)


def _stub_pdf_render_deps() -> None:
    """`chat_pdf_export.render.render` imports jinja2 + markdown_it +
    weasyprint at module top. Tests for `_build_chat_dict` don't render
    anything, but the import chain still has to succeed."""
    jinja2 = _ensure_module("jinja2")

    class _FakeEnv:
        def __init__(self, *a, **k):
            pass

        def get_template(self, *a, **k):
            class _T:
                def render(self_inner, *a, **k):
                    return ""
            return _T()

    jinja2.Environment = _FakeEnv
    jinja2.FileSystemLoader = lambda *a, **k: None
    jinja2.select_autoescape = lambda *a, **k: None

    md = _ensure_module("markdown_it")

    class _FakeMd:
        def __init__(self, *a, **k):
            pass

        def enable(self, *a, **k):
            return self

        def render(self, text):
            return text

    md.MarkdownIt = _FakeMd

    weasy = _ensure_module("weasyprint")

    class _FakeHTML:
        def __init__(self, *a, **k):
            pass

        def write_pdf(self, *a, **k):
            return b"%PDF-fake-bytes"

    weasy.HTML = _FakeHTML
    weasy.CSS = lambda *a, **k: None


def install_all() -> None:
    _stub_helpers()
    _stub_agent()
    _stub_litellm()
    _stub_flask()
    _stub_pdf_render_deps()
