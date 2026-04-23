"""
LLM 토큰 사용량 추적 Extension
- litellm.callbacks에 CustomLogger를 등록하여 모든 LLM 호출 추적
- Telegram Bridge /track webhook으로 자동 전송
"""

import litellm
from litellm.integrations.custom_logger import CustomLogger
import requests
import logging
from helpers.extension import Extension

logger = logging.getLogger(__name__)

TELEGRAM_BRIDGE_URL = "http://telegram-bridge:8443/track"
_callback_registered = False


class UsageLogger(CustomLogger):
    """litellm CustomLogger — 매 LLM 호출 성공 시 토큰/비용 추적"""

    def log_success_event(self, kwargs, response_obj, start_time, end_time):
        """동기 성공 콜백"""
        self._track(kwargs, response_obj)

    async def async_log_success_event(self, kwargs, response_obj, start_time, end_time):
        """비동기 성공 콜백"""
        self._track(kwargs, response_obj)

    def _track(self, kwargs, response_obj):
        try:
            model = kwargs.get("model", "unknown")

            # LiteLLM aggregates streaming chunks into `standard_logging_object`
            # with fully-resolved usage, including Anthropic native cache fields
            # (cache_read_input_tokens / cache_creation_input_tokens from
            # message_start/message_delta). This path works for both streaming
            # (Sonnet chat_model) and non-streaming (Haiku util_model).
            # `response_obj.usage` alone is insufficient for streams because
            # the callback may receive an interim ModelResponseStream rather
            # than the aggregated ModelResponse. See issue #13.
            slo = kwargs.get("standard_logging_object") or {}

            input_tokens = 0
            output_tokens = 0
            cache_read = 0
            cache_creation = 0

            if slo:
                input_tokens = slo.get("prompt_tokens", 0) or 0
                output_tokens = slo.get("completion_tokens", 0) or 0
                cache_read = slo.get("cache_read_input_tokens", 0) or 0
                cache_creation = slo.get("cache_creation_input_tokens", 0) or 0

            # Fallback to response_obj.usage if standard_logging_object missing
            # or empty (older LiteLLM versions, edge cases).
            if input_tokens == 0 and output_tokens == 0:
                usage = getattr(response_obj, "usage", None)
                if usage:
                    input_tokens = getattr(usage, "prompt_tokens", 0) or 0
                    output_tokens = getattr(usage, "completion_tokens", 0) or 0
                    cache_read = getattr(usage, "cache_read_input_tokens", 0) or 0
                    cache_creation = getattr(usage, "cache_creation_input_tokens", 0) or 0
                    # OpenAI cache field
                    details = getattr(usage, "prompt_tokens_details", None)
                    if details is not None:
                        cache_read += getattr(details, "cached_tokens", 0) or 0

            if input_tokens == 0 and output_tokens == 0:
                return

            logger.info(
                f"[UsageTracker] {model}: in={input_tokens}, out={output_tokens}, "
                f"cache_read={cache_read}, cache_creation={cache_creation}"
            )

            # webhook 전송 (fire-and-forget, 실패해도 에이전트에 영향 없음)
            # 신규 필드(cache_read/creation)는 기존 receiver가 무시해도 호환됨.
            try:
                requests.post(
                    TELEGRAM_BRIDGE_URL,
                    json={
                        "model": model,
                        "input_tokens": input_tokens,
                        "output_tokens": output_tokens,
                        "cache_read_tokens": cache_read,
                        "cache_creation_tokens": cache_creation,
                    },
                    timeout=3,
                )
            except Exception:
                pass

        except Exception as e:
            logger.debug(f"UsageTracker error: {e}")


# 싱글턴 인스턴스
_usage_logger = UsageLogger()


class UsageTracker(Extension):

    def execute(self, **kwargs):
        """agent_init 시 litellm.callbacks에 CustomLogger 등록 (1회만)"""
        global _callback_registered

        if _callback_registered:
            return

        if not hasattr(litellm, "callbacks") or litellm.callbacks is None:
            litellm.callbacks = []

        if _usage_logger not in litellm.callbacks:
            litellm.callbacks.append(_usage_logger)
            _callback_registered = True
            logger.info("[UsageTracker] CustomLogger registered to litellm.callbacks")
