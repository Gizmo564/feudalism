#!/usr/bin/env bash
# Install or update the bot on an Ubuntu/Debian machine. Safe to run again: running it IS the update.
#
#   First time:   git clone https://github.com/Gizmo564/feudalism.git ~/feudalism && ~/feudalism/install.sh
#                 (the repo is private: when git asks for a password, paste a GitHub token with read access)
#   Update later: feudalism-update        (or ~/feudalism/install.sh)
set -euo pipefail

REPO="https://github.com/Gizmo564/feudalism.git"
APP_DIR="$(dirname "$(readlink -f "${BASH_SOURCE[0]}")")"      # wherever this script lives
SERVICE="feudalism-bot"
RUN_USER="$(id -un)"

say() { printf '\n== %s\n' "$*"; }

if [ "$(id -u)" -eq 0 ]; then
  echo "Run this as your normal user, not root (it uses sudo only where needed)."; exit 1
fi

say "Installing system packages"
sudo apt-get update -qq
sudo apt-get install -y -qq python3 python3-venv python3-pip git

say "Getting the latest code"
cd "$APP_DIR"
if [ -d .git ]; then
  git config credential.helper store      # remember the GitHub token so updates don't ask again
  git pull --ff-only
else
  echo "Note: $APP_DIR is not a git clone, so I can't pull updates. Clone the repo to get updates."
fi

say "Setting up Python"
[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip install -q --upgrade pip
.venv/bin/pip install -q -r requirements.txt

say "Running the tests (an update that fails them will not be restarted)"
.venv/bin/python -m pytest -q tests

if [ ! -f .env ]; then
  say "First-time settings (saved in $APP_DIR/.env, never committed)"
  read -r -s -p "Discord bot token: " TOKEN; echo
  read -r -p "Discord server (guild) ID, blank for global commands: " GUILD
  read -r -p "Timezone for the nightly tick [America/Denver]: " TZ_IN
  cp .env.example .env
  sed -i "s|^DISCORD_TOKEN=.*|DISCORD_TOKEN=${TOKEN}|" .env
  sed -i "s|^GUILD_ID=.*|GUILD_ID=${GUILD}|" .env
  sed -i "s|^TICK_TZ=.*|TICK_TZ=${TZ_IN:-America/Denver}|" .env
  chmod 600 .env
fi

say "Installing the service"
sudo tee "/etc/systemd/system/${SERVICE}.service" >/dev/null <<UNIT
[Unit]
Description=Feudalism / Population Discord bot
After=network-online.target
Wants=network-online.target

[Service]
User=${RUN_USER}
WorkingDirectory=${APP_DIR}
ExecStart=${APP_DIR}/.venv/bin/python discord_bot.py
Restart=always
RestartSec=10

[Install]
WantedBy=multi-user.target
UNIT
sudo systemctl daemon-reload
sudo systemctl enable "$SERVICE" >/dev/null
sudo systemctl restart "$SERVICE"
sudo ln -sf "${APP_DIR}/install.sh" /usr/local/bin/feudalism-update

sleep 3
say "Status"
systemctl --no-pager --lines=8 status "$SERVICE" || true
cat <<DONE

Done. Useful commands:
  feudalism-update                        pull the latest code from GitHub and restart
  journalctl -u ${SERVICE} -f             watch the bot's log
  sudo systemctl restart ${SERVICE}       restart it
Back up ${APP_DIR}/game.db regularly. It holds the whole game.
DONE
