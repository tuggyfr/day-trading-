#!/usr/bin/env bash
# Run the bot automatically on this Mac.
#   bash autostart_mac.sh install     start now, and again after every login/restart/crash
#   bash autostart_mac.sh status      is it running? plus the last lines of the log
#   bash autostart_mac.sh uninstall   stop it and remove the auto-start
#
# The bot itself waits for market hours, prepares before the open, trades, and closes everything at 15:55 ET,
# every trading day. While it runs, the Mac is kept from idle-sleeping (caffeinate). Keep the Mac plugged in
# and the lid open (or use an external display): a sleeping Mac can't trade.
set -euo pipefail
here="$(cd "$(dirname "$0")" && pwd)"
label="com.daytrading.bot"
plist="$HOME/Library/LaunchAgents/$label.plist"
domain="gui/$(id -u)"

strategy() { grep -E '^BOT_STRATEGY=' "$here/.env" 2>/dev/null | tail -1 | cut -d= -f2 || true; }
paper()    { grep -E '^ALPACA_PAPER_TRADE=' "$here/.env" 2>/dev/null | tail -1 | cut -d= -f2 || true; }

case "${1:-}" in
  install)
    [ -x "$here/.venv/bin/python" ] || { echo "Run: bash setup_mac.sh first." >&2; exit 1; }
    s="$(strategy)"; s="${s:-orb}"
    pt="$(paper)"; pt="${pt:-true}"
    echo "Strategy: $s    Paper trading: $pt"
    if [ "$pt" != "true" ]; then
      echo "Refusing: auto-start is only allowed in paper mode (ALPACA_PAPER_TRADE=true)." >&2; exit 1
    fi
    read -r -p "Start this strategy automatically on every trading day? type yes: " ok
    [ "$ok" = "yes" ] || { echo "Cancelled."; exit 0; }
    mkdir -p "$HOME/Library/LaunchAgents" "$here/logs"
    cat > "$plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0">
<dict>
  <key>Label</key><string>$label</string>
  <key>ProgramArguments</key>
  <array>
    <string>/usr/bin/caffeinate</string><string>-i</string><string>-s</string>
    <string>$here/.venv/bin/python</string><string>-m</string><string>bot</string><string>run</string>
  </array>
  <key>WorkingDirectory</key><string>$here</string>
  <key>RunAtLoad</key><true/>
  <key>KeepAlive</key><true/>
  <key>ThrottleInterval</key><integer>60</integer>
  <key>StandardOutPath</key><string>$here/logs/autostart.out.log</string>
  <key>StandardErrorPath</key><string>$here/logs/autostart.err.log</string>
</dict>
</plist>
PLIST
    launchctl bootout "$domain/$label" 2>/dev/null || true
    launchctl bootstrap "$domain" "$plist"
    echo "Installed. The bot is running now and will restart by itself after a crash, restart or login."
    echo "Check on it any time with: bash autostart_mac.sh status"
    ;;
  status)
    if launchctl print "$domain/$label" >/dev/null 2>&1; then
      launchctl print "$domain/$label" | grep -E '^\s*(state|pid|last exit code)' || true
    else
      echo "Not installed."
    fi
    echo "--- last log lines (logs/bot.log):"
    tail -n 15 "$here/logs/bot.log" 2>/dev/null || echo "(no log yet)"
    ;;
  uninstall)
    launchctl bootout "$domain/$label" 2>/dev/null || true
    rm -f "$plist"
    echo "Stopped and removed. (Open positions are protected by their stop-losses; run"
    echo "'.venv/bin/python -m bot flatten' if you also want to close them now.)"
    ;;
  *)
    echo "usage: bash autostart_mac.sh install | status | uninstall" >&2; exit 1 ;;
esac
