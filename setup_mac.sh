#!/usr/bin/env bash
# One-time setup on a Mac. Run from the project folder:  bash setup_mac.sh
# Installs uv (a Python installer from Astral), Python 3.12 just for this project, and the bot's packages.
# Nothing is installed system-wide except uv in ~/.local/bin.
set -euo pipefail
cd "$(dirname "$0")"

echo "==> 1/5 Checking for uv"
if ! command -v uv >/dev/null 2>&1; then
  curl -LsSf https://astral.sh/uv/install.sh | sh
fi
export PATH="$HOME/.local/bin:$PATH"

echo "==> 2/5 Creating a private Python 3.12 environment in .venv"
uv venv --python 3.12 --allow-existing .venv

echo "==> 3/5 Installing the bot's packages"
uv pip install --python .venv/bin/python -r requirements-dev.txt

echo "==> 4/5 Running the tests"
.venv/bin/python -m pytest -q

echo "==> 5/5 Preparing your settings file (.env)"
if [ ! -f .env ]; then
  cp .env.example .env
fi
chmod 600 .env
mkdir -p logs && chmod 700 logs

echo
echo "Setup finished. Opening .env in TextEdit now."
echo "Paste your Alpaca PAPER keys after ALPACA_API_KEY= and ALPACA_SECRET_KEY= (no spaces), then save and close."
open -e .env
