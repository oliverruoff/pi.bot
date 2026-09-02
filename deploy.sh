#!/usr/bin/env bash
set -Eeuo pipefail

APP_DIR="${APP_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)}"
BRANCH="${BRANCH:-main}"
SERVICE_NAME="${SERVICE_NAME:-servicebot-control}"
SERVICE_USER="${SERVICE_USER:-$(id -un)}"
NODE_VERSION="${NODE_VERSION:-22.19.0}"
PI_VERSION="${PI_VERSION:-0.84.4}"
NODE_DIR="/opt/node-v${NODE_VERSION}-linux-arm64"

log() { printf '\n[%s] %s\n' "$(date '+%H:%M:%S')" "$*"; }
fail() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
need() { command -v "$1" >/dev/null 2>&1 || fail "Missing command: $1"; }

need git
need sudo
cd "$APP_DIR"
test -d .git || fail "$APP_DIR is not a Git checkout. Clone the repository first."
test -f .env || fail "Missing $APP_DIR/.env. Copy .env.example and add the provider key first."

log "Updating repository"
git fetch origin "$BRANCH"
git checkout "$BRANCH"
git pull --ff-only origin "$BRANCH"

node_is_compatible=false
if command -v node >/dev/null 2>&1; then
  node_major="$(node --version | sed -E 's/^v([0-9]+).*/\1/')"
  if [ "$node_major" -ge 22 ]; then node_is_compatible=true; fi
fi

if [ "$node_is_compatible" != true ]; then
  test "$(uname -m)" = "aarch64" || fail "Automatic Node installation supports Raspberry Pi OS 64-bit (aarch64) only. Install Node >=22.19 manually."
  need curl
  tmp_dir="$(mktemp -d)"
  trap 'rm -rf "$tmp_dir"' EXIT
  archive="$tmp_dir/node.tar.xz"
  log "Installing Node $NODE_VERSION for ARM64"
  curl -fL "https://nodejs.org/dist/v${NODE_VERSION}/node-v${NODE_VERSION}-linux-arm64.tar.xz" -o "$archive"
  if [ ! -d "$NODE_DIR" ]; then sudo tar -xJf "$archive" -C /opt; fi
  sudo ln -sfn "$NODE_DIR/bin/node" /usr/local/bin/node
  sudo ln -sfn "$NODE_DIR/bin/npm" /usr/local/bin/npm
  sudo ln -sfn "$NODE_DIR/bin/npx" /usr/local/bin/npx
fi

if ! command -v pi >/dev/null 2>&1; then
  log "Installing Pi Coding Agent"
  sudo env PATH=/usr/local/bin:/usr/bin:/bin /usr/local/bin/npm install -g "@earendil-works/pi-coding-agent@$PI_VERSION"
  if [ -x "$NODE_DIR/bin/pi" ]; then sudo ln -sfn "$NODE_DIR/bin/pi" /usr/local/bin/pi; fi
fi

command -v pi >/dev/null 2>&1 || fail "Pi installation did not create an executable"
mkdir -p "$APP_DIR/data"
chmod 600 "$APP_DIR/.env"

service_tmp="$(mktemp)"
trap 'rm -rf "$service_tmp" "${tmp_dir:-}"' EXIT
sed \
  -e "s|^User=.*|User=$SERVICE_USER|" \
  -e "s|^Group=.*|Group=$SERVICE_USER|" \
  -e "s|/home/bot/pi.bot|$APP_DIR|g" \
  webserver/servicebot-control.service > "$service_tmp"

log "Installing and restarting $SERVICE_NAME"
sudo install -m 0644 "$service_tmp" "/etc/systemd/system/$SERVICE_NAME.service"
sudo systemctl daemon-reload
sudo systemctl enable "$SERVICE_NAME"
sudo systemctl restart "$SERVICE_NAME"
health_ok=false
for _attempt in $(seq 1 30); do
  if curl -fsS http://127.0.0.1:8080/api/status > /tmp/pi-bot-deploy-status.json 2>/dev/null; then
    health_ok=true
    break
  fi
  sleep 2
done
if [ "$health_ok" != true ]; then
  sudo journalctl -u "$SERVICE_NAME" --no-pager -n 80
  fail "pi.bot did not become healthy within 60 seconds"
fi
sudo systemctl --no-pager --full status "$SERVICE_NAME"
cat /tmp/pi-bot-deploy-status.json
rm -f /tmp/pi-bot-deploy-status.json
printf '\n'
log "Deployment complete"
