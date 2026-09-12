#!/usr/bin/env bash
set -Eeuo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$ROOT_DIR/backend"
FRONTEND_DIR="$ROOT_DIR/frontend"
VENV_DIR="$BACKEND_DIR/.venv-local"
LOCAL_DIR="$ROOT_DIR/.local"
APP_URL="http://127.0.0.1:8080"

find_python() {
  local candidate version_ok
  for candidate in python3.13 python3.12 python3.11 python3.10 python3; do
    if command -v "$candidate" >/dev/null 2>&1; then
      version_ok="$($candidate -c 'import sys; print(int(sys.version_info >= (3, 10)))')"
      if [[ "$version_ok" == "1" ]]; then
        command -v "$candidate"
        return 0
      fi
    fi
  done
  return 1
}

PYTHON_BIN="$(find_python || true)"
if [[ -z "$PYTHON_BIN" ]]; then
  echo "Python 3.10 or newer is required. Install it and run this file again."
  exit 1
fi
if ! command -v node >/dev/null 2>&1 || ! command -v npm >/dev/null 2>&1; then
  echo "Node.js and npm are required. Install Node.js and run this file again."
  exit 1
fi

mkdir -p "$LOCAL_DIR"
if [[ ! -x "$VENV_DIR/bin/python" ]]; then
  echo "Preparing the local Python environment..."
  "$PYTHON_BIN" -m venv "$VENV_DIR"
fi

REQUIREMENTS_HASH="$(cat "$BACKEND_DIR/requirements.txt" "$BACKEND_DIR/requirements-collector.txt" "$BACKEND_DIR/requirements-dev.txt" | shasum -a 256 | awk '{print $1}')"
INSTALLED_HASH="$(cat "$VENV_DIR/.requirements-hash" 2>/dev/null || true)"
if [[ "$REQUIREMENTS_HASH" != "$INSTALLED_HASH" ]]; then
  echo "Installing backend and Scrapling packages..."
  "$VENV_DIR/bin/python" -m pip install --upgrade pip
  "$VENV_DIR/bin/python" -m pip install -r "$BACKEND_DIR/requirements-dev.txt"
  printf '%s' "$REQUIREMENTS_HASH" > "$VENV_DIR/.requirements-hash"
fi

if [[ ! -f "$VENV_DIR/.scrapling-browser-ready" ]]; then
  echo "Installing Scrapling's browser (first launch only)..."
  "$VENV_DIR/bin/scrapling" install
  touch "$VENV_DIR/.scrapling-browser-ready"
fi

if [[ ! -d "$FRONTEND_DIR/node_modules" ]]; then
  echo "Installing frontend packages..."
  (cd "$FRONTEND_DIR" && npm ci)
fi

echo "Building the interface..."
(cd "$FRONTEND_DIR" && npm run build)

if [[ ! -f "$LOCAL_DIR/session-secret" ]]; then
  "$VENV_DIR/bin/python" -c 'import secrets; print(secrets.token_urlsafe(48))' > "$LOCAL_DIR/session-secret"
  chmod 600 "$LOCAL_DIR/session-secret"
fi

TEAM_PASSWORD="${TEAM_PASSWORD:-instatrack}"
TEAM_PASSWORD_HASH="$($VENV_DIR/bin/python -c 'from argon2 import PasswordHasher; import sys; print(PasswordHasher().hash(sys.argv[1]))' "$TEAM_PASSWORD")"
SESSION_SECRET="$(cat "$LOCAL_DIR/session-secret")"
DATABASE_URL="sqlite:///$LOCAL_DIR/instatrack.db"

if curl --silent --fail "$APP_URL/api/health" >/dev/null 2>&1; then
  echo "InstaTrack is already running at $APP_URL"
else
  echo
  echo "Starting InstaTrack at $APP_URL"
  echo "Team password: $TEAM_PASSWORD"
  echo "Press Control-C to stop it."
  echo
  cd "$BACKEND_DIR"
  DATABASE_URL="$DATABASE_URL" \
  TEAM_PASSWORD_HASH="$TEAM_PASSWORD_HASH" \
  SESSION_SECRET="$SESSION_SECRET" \
  SECURE_COOKIES=false \
  LOCAL_COLLECTOR_ENABLED=true \
  COLLECTOR_ADAPTER=scrapling \
  FRONTEND_DIST_DIR="$FRONTEND_DIR/dist" \
  PORT=8080 \
  "$VENV_DIR/bin/python" -m app.serve &
  SERVER_PID=$!
  trap 'kill "$SERVER_PID" 2>/dev/null || true; wait "$SERVER_PID" 2>/dev/null || true' EXIT INT TERM

  for _ in {1..60}; do
    if curl --silent --fail "$APP_URL/api/health" >/dev/null 2>&1; then
      break
    fi
    if ! kill -0 "$SERVER_PID" 2>/dev/null; then
      wait "$SERVER_PID"
      exit 1
    fi
    sleep 0.25
  done
fi

if [[ "${OPEN_BROWSER:-1}" == "1" ]]; then
  if command -v open >/dev/null 2>&1; then
    open "$APP_URL"
  elif command -v xdg-open >/dev/null 2>&1; then
    xdg-open "$APP_URL"
  fi
fi

if [[ -n "${SERVER_PID:-}" ]]; then
  wait "$SERVER_PID"
fi
