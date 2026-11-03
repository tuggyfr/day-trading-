"""Send alerts to Telegram and/or Discord. Falls back to the log if neither is configured."""

from __future__ import annotations

import logging
import time

import requests

log = logging.getLogger("bot.notify")

MAX_LEN = 1900          # Discord's limit is 2000 characters, Telegram's 4096
MAX_PER_MINUTE = 20     # an error loop must not spam you or get the bot banned by Telegram


class Redactor:
    """Replaces any configured secret with '***' wherever it appears in text."""

    def __init__(self, secrets: list[str]):
        # only redact non-trivial values; longest first so overlapping secrets are fully hidden
        self.secrets = sorted({s for s in secrets if s and len(s) >= 6}, key=len, reverse=True)

    def __call__(self, text: str) -> str:
        for s in self.secrets:
            text = text.replace(s, "***")
        return text


class RedactingFilter(logging.Filter):
    """Logging filter that scrubs secrets from every log line (console and logs/bot.log)."""

    def __init__(self, redactor: Redactor):
        super().__init__()
        self.redact = redactor

    def filter(self, record: logging.LogRecord) -> bool:
        msg = record.getMessage()
        clean = self.redact(msg)
        if clean != msg:
            record.msg, record.args = clean, None
        return True


class Notifier:
    def __init__(
        self,
        telegram_token: str = "",
        telegram_chat_id: str = "",
        discord_webhook: str = "",
        redactor: Redactor | None = None,
        clock=time.monotonic,
    ):
        self.telegram_token = telegram_token
        self.telegram_chat_id = telegram_chat_id
        self.discord_webhook = discord_webhook
        self.redact = redactor or Redactor([telegram_token, discord_webhook])
        self._clock = clock
        self._sent: list[float] = []

    def _allowed(self) -> bool:
        now = self._clock()
        self._sent = [t for t in self._sent if now - t < 60]
        if len(self._sent) >= MAX_PER_MINUTE:
            return False
        self._sent.append(now)
        return True

    def send(self, text: str) -> None:
        text = self.redact(text)
        if len(text) > MAX_LEN:
            text = text[: MAX_LEN - 3] + "..."
        log.info("ALERT: %s", text)
        if not (self.telegram_token or self.discord_webhook):
            return
        if not self._allowed():
            log.warning("alert rate limit hit; not forwarding to Telegram/Discord")
            return
        if self.telegram_token and self.telegram_chat_id:
            self._post(
                f"https://api.telegram.org/bot{self.telegram_token}/sendMessage",
                {"chat_id": self.telegram_chat_id, "text": text},
            )
        if self.discord_webhook:
            self._post(self.discord_webhook, {"content": text})

    @staticmethod
    def _post(url: str, payload: dict) -> None:
        try:
            requests.post(url, json=payload, timeout=5).raise_for_status()
        except Exception as exc:  # alerts must never crash the bot; never log the URL (it holds the token)
            log.warning("notification failed: %s", exc.__class__.__name__)
