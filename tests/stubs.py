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

    # `helpers.notification` — chat_pdf_export emits PDF-generation progress
    # toasts via NotificationManager (issue #133). Record every send so tests
    # can assert the progress → success/error transitions.
    notif_mod = _ensure_module("helpers.notification")

    class _NotificationType:
        INFO = "info"
        SUCCESS = "success"
        WARNING = "warning"
        ERROR = "error"
        PROGRESS = "progress"

    class _NotificationPriority:
        NORMAL = 10
        HIGH = 20

    class _NotificationManager:
        sent: list = []

        @staticmethod
        def send_notification(**kwargs):
            _NotificationManager.sent.append(kwargs)
            return kwargs

    notif_mod.NotificationType = _NotificationType
    notif_mod.NotificationPriority = _NotificationPriority
    notif_mod.NotificationManager = _NotificationManager
    helpers.notification = notif_mod  # type: ignore[attr-defined]


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


def _stub_aiohttp() -> None:
    """`pricing.snapshot._fetch_litellm_table` imports aiohttp for the
    LiteLLM HTTP fetch. Pure-helper tests don't exercise that path, but
    the module-level import still has to succeed when aiohttp isn't on
    the test host's pip list."""
    if "aiohttp" in sys.modules:
        return
    aiohttp = _ensure_module("aiohttp")

    class _FakeClientTimeout:
        def __init__(self, *a, **k):
            pass

    class _FakeClientSession:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return None

        def get(self, *a, **k):
            raise NotImplementedError("aiohttp stub — fetch path not exercised in unit tests")

    class _FakeCookieJar:
        def __init__(self, *a, **k):
            pass

    aiohttp.ClientTimeout = _FakeClientTimeout
    aiohttp.ClientSession = _FakeClientSession
    aiohttp.CookieJar = _FakeCookieJar

    # `aiohttp.web` — webhook handlers + dashboard handlers use it. We
    # don't actually start a server in tests; we just need the module
    # surface to satisfy imports + return inspectable Response/json_response
    # objects so handler tests can read body bytes.
    web = _ensure_module("aiohttp.web")

    class _FakeResponse:
        def __init__(self, *, status=200, body=b"", text=None,
                     content_type=None, charset=None, headers=None):
            self.status = status
            self.body = body if isinstance(body, bytes | bytearray) else (
                (text or "").encode("utf-8")
            )
            self.text = text or ""
            self.content_type = content_type
            self.charset = charset
            self.headers = headers or {}

    def _json_response(data=None, *, status=200, headers=None):
        import json as _json
        body = _json.dumps(data).encode("utf-8")
        return _FakeResponse(status=status, body=body, headers=headers)

    class _FakeApp:
        def __init__(self):
            self.router = _FakeRouter()

    class _FakeRouter:
        def __init__(self):
            self.routes = []

        def add_post(self, path, handler):
            self.routes.append(("POST", path, handler))

        def add_get(self, path, handler):
            self.routes.append(("GET", path, handler))

    class _FakeAppRunner:
        def __init__(self, *a, **k):
            pass

        async def setup(self):
            pass

    class _FakeTCPSite:
        def __init__(self, *a, **k):
            pass

        async def start(self):
            pass

    web.Response = _FakeResponse
    web.json_response = _json_response
    web.Application = _FakeApp
    web.AppRunner = _FakeAppRunner
    web.TCPSite = _FakeTCPSite
    aiohttp.web = web


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


def _stub_telegram() -> None:
    """`from telegram import Update, Bot` and `from telegram.ext import …`.

    bot.py and the telegram_handlers/* modules import these at module top.
    Tests that load those modules (e.g. test_bridge_optional_telegram)
    only need the names to resolve — no behavior. Skip if the real
    library is on the host (won't happen in CI's slim env)."""
    if "telegram" in sys.modules:
        return
    telegram = _ensure_module("telegram")

    class _FakeUpdate:
        ALL_TYPES = "ALL_TYPES"

    class _FakeBot:
        def __init__(self, *a, **k):
            pass

    telegram.Update = _FakeUpdate
    telegram.Bot = _FakeBot

    ext = _ensure_module("telegram.ext")

    class _FakeApplication:
        @classmethod
        def builder(cls):
            return _FakeBuilder()

        def add_handler(self, *a, **k):
            pass

        def run_polling(self, *a, **k):
            pass

    class _FakeBuilder:
        def token(self, *a, **k):
            return self

        def post_init(self, *a, **k):
            return self

        def build(self):
            return _FakeApplication()

    class _FakeHandler:
        def __init__(self, *a, **k):
            pass

    class _FakeFilters:
        TEXT = object()
        COMMAND = object()

        def __invert__(self):
            return self

        def __and__(self, other):
            return self

    class _FakeContextTypes:
        DEFAULT_TYPE = object

    ext.Application = _FakeApplication
    ext.CommandHandler = _FakeHandler
    ext.MessageHandler = _FakeHandler
    ext.filters = _FakeFilters()
    ext.ContextTypes = _FakeContextTypes
    telegram.ext = ext  # type: ignore[attr-defined]


def install_all() -> None:
    _stub_helpers()
    _stub_agent()
    _stub_litellm()
    _stub_flask()
    _stub_pdf_render_deps()
    _stub_aiohttp()
    _stub_telegram()
