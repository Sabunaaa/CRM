#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
BRANCH="${VM_BRANCH:-main}"
PASSWORD_VENV="$ROOT_DIR/backend/.venv-vm"
COMPOSE=(sudo docker compose)

cd "$ROOT_DIR"

if [[ ! -f docker-compose.yml || ! -d .git ]]; then
  echo "Run this script from a cloned CRM repository." >&2
  exit 1
fi

wait_for_package_manager() {
  local attempts=0
  while sudo fuser /var/lib/dpkg/lock-frontend >/dev/null 2>&1 \
     || sudo fuser /var/lib/apt/lists/lock >/dev/null 2>&1; do
    attempts=$((attempts + 1))
    if (( attempts > 60 )); then
      echo "Ubuntu's package manager is still busy. Try again later." >&2
      exit 1
    fi
    echo "Waiting for Ubuntu's automatic package update to finish..."
    sleep 5
  done
}

vm_dependencies_ready() {
  command -v git >/dev/null 2>&1 \
    && command -v python3 >/dev/null 2>&1 \
    && command -v openssl >/dev/null 2>&1 \
    && command -v docker >/dev/null 2>&1 \
    && sudo docker compose version >/dev/null 2>&1 \
    && systemctl list-unit-files cron.service >/dev/null 2>&1
}

if vm_dependencies_ready; then
  echo "VM dependencies are already installed."
else
  echo "Installing VM dependencies (first run only)..."
  wait_for_package_manager
  sudo apt-get update
  sudo DEBIAN_FRONTEND=noninteractive apt-get install -y \
    cron docker.io docker-compose-v2 git python3-venv openssl
fi
sudo systemctl enable --now docker
sudo systemctl enable --now cron

echo "Updating the repository..."
git pull --ff-only origin "$BRANCH"

if [[ ! -f .env ]]; then
  echo "Creating the first-run secrets file. It will be reused on future updates."
  read -r -s -p "Choose the shared CRM password (minimum 8 characters): " TEAM_PASSWORD
  echo
  read -r -s -p "Confirm the shared CRM password: " TEAM_PASSWORD_CONFIRM
  echo
  if [[ "$TEAM_PASSWORD" != "$TEAM_PASSWORD_CONFIRM" ]]; then
    echo "Passwords do not match." >&2
    exit 2
  fi
  if (( ${#TEAM_PASSWORD} < 8 )); then
    echo "The team password must contain at least 8 characters." >&2
    exit 2
  fi

  if [[ ! -x "$PASSWORD_VENV/bin/python" ]]; then
    python3 -m venv "$PASSWORD_VENV"
    "$PASSWORD_VENV/bin/python" -m pip install --quiet argon2-cffi
  fi

  TEAM_PASSWORD_HASH="$(printf '%s' "$TEAM_PASSWORD" | "$PASSWORD_VENV/bin/python" -c \
    'from argon2 import PasswordHasher; import sys; print(PasswordHasher().hash(sys.stdin.read()))')"
  SESSION_SECRET="$(openssl rand -hex 32)"
  umask 077
  printf "TEAM_PASSWORD_HASH='%s'\nSESSION_SECRET='%s'\n" \
    "$TEAM_PASSWORD_HASH" "$SESSION_SECRET" > .env
  unset TEAM_PASSWORD TEAM_PASSWORD_CONFIRM TEAM_PASSWORD_HASH SESSION_SECRET
else
  if ! grep -q '^TEAM_PASSWORD_HASH=' .env || ! grep -q '^SESSION_SECRET=' .env; then
    echo ".env exists but is missing TEAM_PASSWORD_HASH or SESSION_SECRET." >&2
    exit 2
  fi
fi

chmod 600 .env
echo "Building the shared application image..."
"${COMPOSE[@]}" build api
echo "Starting the CRM..."
"${COMPOSE[@]}" up -d --remove-orphans

CRON_FILE=/etc/cron.d/instatrack-collector
sudo tee "$CRON_FILE" >/dev/null <<EOF
SHELL=/bin/bash
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin
CRON_TZ=Asia/Tbilisi
0 0,12 * * * root cd $ROOT_DIR && /usr/bin/flock -n /var/lock/instatrack-collector.lock /usr/bin/docker compose --profile manual run --rm collector --trigger scheduled >> /var/log/instatrack-collector.log 2>&1
EOF
sudo chmod 644 "$CRON_FILE"

echo
echo "CRM is running."
"${COMPOSE[@]}" ps
echo "Open http://YOUR_VM_EXTERNAL_IP:8080 until HTTPS is configured."
echo "Future updates: ./scripts/update_vm.sh"
