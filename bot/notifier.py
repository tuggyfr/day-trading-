"""Send alerts to Telegram and/or Discord. Falls back to the log if neither is configured."""

from __future__ import annotations

import logging

import requests

log = logging.getLogger("bot.notify")


class Notifier:
    def __init__(self, telegram_token: str = "", telegram_chat_id: str = "", discord_webhook: str = ""):
        self.telegram_token = telegram_token
        self.telegram_chat_id = telegram_chat_id
        self.discord_webhook = discord_webhook

    def send(self, text: str) -> None:
        log.info("ALERT: %s", text)
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
        except Exception as exc:  # alerts must never crash the bot
            log.warning("notification failed: %s", exc.__class__.__name__)
