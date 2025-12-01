import logging
from contextvars import ContextVar
from typing import Optional

guild_id: ContextVar[Optional[int]] = ContextVar("guild_id", default=None)
channel_id: ContextVar[Optional[int]] = ContextVar("channel_id", default=None)


class ContextualLogger(logging.LoggerAdapter):
    def process(self, msg: str, kwargs: dict) -> tuple[str, dict]:  # type: ignore
        context = []

        if gid := guild_id.get():
            context.append(f"guild:{gid}")
        if cid := channel_id.get():
            context.append(f"channel:{cid}")

        if context:
            msg = f"[{' '.join(context)}] {msg}"

        return msg, kwargs


def get_logger(name: str) -> ContextualLogger:
    base_logger = logging.getLogger(name)
    return ContextualLogger(base_logger, {})
