"""Guards for the security fixes: secrets never committed or logged, settings bounded, API rate-limited."""

import logging
import re
import subprocess
from pathlib import Path

import pytest

from bot.broker import RateLimiter
from bot.config import RiskParams, Settings, StrategyParams
from bot.notifier import MAX_LEN, MAX_PER_MINUTE, Notifier, RedactingFilter, Redactor

ROOT = Path(__file__).resolve().parent.parent

SECRET_PATTERNS = [
    re.compile(r"\b(PK|AK|CK)[A-Z0-9]{16,}\b"),                          # Alpaca key IDs
    re.compile(r"\b\d{8,10}:[A-Za-z0-9_-]{35}\b"),                       # Telegram bot token
    re.compile(r"discord(app)?\.com/api/webhooks/\d+/[\w-]+"),          # Discord webhook
    re.compile(r"(?i)apikey=[A-Za-z0-9]{10,}"),                          # key in a URL
    re.compile(r"(?m)^(ALPACA_SECRET_KEY|ALPACA_API_KEY|TELEGRAM_BOT_TOKEN)=\S+"),  # filled-in env line
]


def test_no_secrets_in_tracked_files():
    files = subprocess.check_output(["git", "ls-files"], cwd=ROOT, text=True).split()
    hits = []
    for f in files:
        text = (ROOT / f).read_text(errors="ignore")
        for pat in SECRET_PATTERNS:
            for m in pat.finditer(text):
                hits.append(f"{f}: {m.group(0)[:12]}...")
    assert not hits, f"possible secrets committed: {hits}"


def test_env_file_is_gitignored():
    out = subprocess.run(["git", "check-ignore", ".env", "logs/bot.log"], cwd=ROOT, capture_output=True, text=True)
    assert ".env" in out.stdout and "logs/bot.log" in out.stdout


def _settings(**risk):
    return Settings(api_key="k", secret_key="s", symbols=["SPY"], risk=RiskParams(**risk))


def test_percent_typo_is_rejected():
    _settings().validate_params()  # defaults are fine
    with pytest.raises(SystemExit, match="RISK_PER_TRADE_PCT"):
        _settings(risk_per_trade_pct=0.5).validate_params()  # meant 0.5%, wrote 50%
    with pytest.raises(SystemExit, match="RISK_MAX_DAILY_LOSS_PCT"):
        _settings(max_daily_loss_pct=2).validate_params()


def test_bad_symbols_and_settings_rejected():
    with pytest.raises(SystemExit, match="invalid symbols"):
        Settings(symbols=["SPY", "../etc", "A;RM"]).validate_params()
    with pytest.raises(SystemExit, match="ORB_STOP_MODE"):
        Settings(symbols=["SPY"], strategy=StrategyParams(stop_mode="yolo")).validate_params()
    with pytest.raises(SystemExit, match="BOT_POLL_SECONDS"):
        Settings(symbols=["SPY"], poll_seconds=1).validate_params()
    with pytest.raises(SystemExit, match="https"):
        Settings(symbols=["SPY"], discord_webhook_url="http://x").validate_params()
    Settings(symbols=["SPY", "BRK.B", "BTC/USD"]).validate_params()


def test_rate_limiter_blocks_when_full():
    now = [0.0]
    slept = []

    def sleep(s):
        slept.append(s)
        now[0] += s

    rl = RateLimiter(max_calls=3, per_seconds=60, clock=lambda: now[0], sleep=sleep)
    for _ in range(3):
        rl.acquire()
    assert slept == []
    rl.acquire()  # 4th call within the window must wait ~60s
    assert slept and slept[0] == pytest.approx(60)


def test_secrets_redacted_from_alerts_and_logs(caplog):
    secret = "PK" + "fake" * 5  # built at runtime so the committed-secrets scan above doesn't flag it
    r = Redactor([secret, "", "abc"])
    assert r(f"error with key {secret}") == "error with key ***"

    logger = logging.getLogger("test.redact")
    f = RedactingFilter(r)
    logger.addFilter(f)
    with caplog.at_level(logging.INFO, logger="test.redact"):
        logger.info("request failed for %s", secret)
    logger.removeFilter(f)
    assert secret not in caplog.text and "***" in caplog.text


def test_alerts_truncated_and_rate_limited(monkeypatch):
    posts = []
    monkeypatch.setattr(Notifier, "_post", staticmethod(lambda url, payload: posts.append(payload)))
    n = Notifier(discord_webhook="https://discord.example/hook", clock=lambda: 0.0)
    n.send("x" * 5000)
    assert len(posts[0]["content"]) <= MAX_LEN
    for _ in range(100):
        n.send("loop")
    assert len(posts) == MAX_PER_MINUTE
