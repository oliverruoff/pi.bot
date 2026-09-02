#!/usr/bin/env bash
set -Eeuo pipefail
APP_DIR="${APP_DIR:-/home/bot/Navibot}"
BRANCH="${BRANCH:-main}"
cd "$APP_DIR"
git fetch origin "$BRANCH"
git checkout "$BRANCH"
git pull --ff-only origin "$BRANCH"
test -f .env || { echo "Missing $APP_DIR/.env" >&2; exit 1; }
command -v pi >/dev/null || sudo npm install -g @earendil-works/pi-coding-agent@latest
sudo cp webserver/servicebot-control.service /etc/systemd/system/servicebot-control.service
sudo systemctl daemon-reload
sudo systemctl enable --now servicebot-control
sudo systemctl restart servicebot-control
sudo systemctl --no-pager --full status servicebot-control
