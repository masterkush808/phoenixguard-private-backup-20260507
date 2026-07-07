#!/usr/bin/env bash
set -Eeuo pipefail

REPO_URL="${REPO_URL:-https://github.com/masterkush808/phoenixguard-private-backup-20260507.git}"
BRANCH="${BRANCH:-main}"
INSTALL_ROOT="${INSTALL_ROOT:-/opt/phoenixguard}"
SERVICE_USER="${SERVICE_USER:-phoenixguard}"
SESSION_ID="${SESSION_ID:-cloud-live}"
API_HOST="${API_HOST:-127.0.0.1}"
API_PORT="${API_PORT:-8793}"
DOMAIN="${DOMAIN:-}"
CLOUDFLARED_TOKEN="${CLOUDFLARED_TOKEN:-}"
FRAME_INGEST_TOKEN="${FRAME_INGEST_TOKEN:-}"
ASSET_ARCHIVE_URL="${ASSET_ARCHIVE_URL:-}"

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Run as root on the VPS: sudo -E bash Developer/deployment/linux_cloud_brain_bootstrap.sh" >&2
  exit 2
fi

if [[ -z "${FRAME_INGEST_TOKEN}" ]]; then
  FRAME_INGEST_TOKEN="$(openssl rand -hex 32)"
fi

REPO_ROOT="${INSTALL_ROOT}/phoenixguard"
ENV_DIR="/etc/phoenixguard"
ENV_FILE="${ENV_DIR}/cloud-brain.env"
FEED_TOKEN_REGISTRY="${ENV_DIR}/frame_ingest_token_registry.json"

echo "[PhoenixGuard Cloud Brain] Installing OS packages."
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y \
  ca-certificates \
  curl \
  git \
  openssl \
  build-essential \
  libgomp1 \
  libglib2.0-0 \
  libsm6 \
  libxext6 \
  libxrender1 \
  tesseract-ocr \
  unzip \
  zstd

if ! id "${SERVICE_USER}" >/dev/null 2>&1; then
  useradd --system --create-home --shell /usr/sbin/nologin "${SERVICE_USER}"
fi

mkdir -p "${INSTALL_ROOT}" "${ENV_DIR}"
chown -R "${SERVICE_USER}:${SERVICE_USER}" "${INSTALL_ROOT}"

echo "[PhoenixGuard Cloud Brain] Installing uv and Python 3.11."
if ! command -v uv >/dev/null 2>&1; then
  curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin sh
fi
uv python install 3.11

if [[ ! -d "${REPO_ROOT}/.git" ]]; then
  echo "[PhoenixGuard Cloud Brain] Cloning ${REPO_URL}."
  sudo -u "${SERVICE_USER}" git clone --branch "${BRANCH}" "${REPO_URL}" "${REPO_ROOT}"
else
  echo "[PhoenixGuard Cloud Brain] Updating ${REPO_ROOT}."
  sudo -u "${SERVICE_USER}" git -C "${REPO_ROOT}" fetch origin
  sudo -u "${SERVICE_USER}" git -C "${REPO_ROOT}" checkout "${BRANCH}"
  sudo -u "${SERVICE_USER}" git -C "${REPO_ROOT}" pull --ff-only origin "${BRANCH}"
fi

echo "[PhoenixGuard Cloud Brain] Creating .venv-live."
sudo -u "${SERVICE_USER}" uv venv "${REPO_ROOT}/.venv-live" --python 3.11
sudo -u "${SERVICE_USER}" uv pip install --python "${REPO_ROOT}/.venv-live/bin/python" -r "${REPO_ROOT}/requirements/locks/live-linux-py311.txt"

if [[ -n "${ASSET_ARCHIVE_URL}" ]]; then
  echo "[PhoenixGuard Cloud Brain] Downloading asset archive."
  tmp_asset="/tmp/phoenixguard-assets"
  curl -fL "${ASSET_ARCHIVE_URL}" -o "${tmp_asset}"
  if file "${tmp_asset}" | grep -qi "zip"; then
    sudo -u "${SERVICE_USER}" unzip -o "${tmp_asset}" -d "${REPO_ROOT}"
  else
    sudo -u "${SERVICE_USER}" tar --zstd -xf "${tmp_asset}" -C "${REPO_ROOT}"
  fi
  rm -f "${tmp_asset}"
fi

sudo -u "${SERVICE_USER}" mkdir -p \
  "${REPO_ROOT}/runtime/live/logs_live" \
  "${REPO_ROOT}/runtime/live/data_live"

ALLOWED_ORIGINS=""
TRUSTED_HOSTS="127.0.0.1,localhost"
if [[ -n "${DOMAIN}" ]]; then
  ALLOWED_ORIGINS="https://${DOMAIN}"
  TRUSTED_HOSTS="${DOMAIN},127.0.0.1,localhost"
fi

cat > "${FEED_TOKEN_REGISTRY}" <<EOF
{
  "schema_version": "PG_FRAME_INGEST_TOKEN_REGISTRY_V1",
  "tokens": [
    {
      "name": "deployment-admin-feed",
      "enabled": true,
      "user_id": "deployment-admin",
      "token_env": "PHOENIXGUARD_FEED_TOKEN_ADMIN",
      "max_active_feeds": 1,
      "min_interval_sec": 10
    }
  ]
}
EOF
chmod 0640 "${FEED_TOKEN_REGISTRY}"
chown root:"${SERVICE_USER}" "${FEED_TOKEN_REGISTRY}"

cat > "${ENV_FILE}" <<EOF
PHOENIXGUARD_PYTHON_PROFILE=live
PHOENIXGUARD_PYTHON_ENV_NAME=.venv-live
PHOENIXGUARD_PYTHON_EXE=${REPO_ROOT}/.venv-live/bin/python
PHOENIXGUARD_MOBILE_API_HOST=${API_HOST}
PHOENIXGUARD_MOBILE_API_PORT=${API_PORT}
PHOENIXGUARD_TRACKER_SESSION_ID=${SESSION_ID}
PHOENIXGUARD_RUNTIME_DIR=${REPO_ROOT}/runtime/live
PHOENIXGUARD_DATA_DIR=${REPO_ROOT}/runtime/live/data_live
PHOENIXGUARD_LOGS_DIR=${REPO_ROOT}/runtime/live/logs_live
PHOENIXGUARD_FRAME_INGEST_TOKEN_REGISTRY=${FEED_TOKEN_REGISTRY}
PHOENIXGUARD_FEED_TOKEN_ADMIN=${FRAME_INGEST_TOKEN}
PHOENIXGUARD_FRAME_INGEST_MAX_SOURCE_AGE_SEC=180
PHOENIXGUARD_FRAME_INGEST_REQUIRE_CAPTURE_EPOCH=1
PHOENIXGUARD_FRAME_INGEST_REQUIRE_FRAME_ID=1
PHOENIXGUARD_FRAME_INGEST_MIN_INTERVAL_SEC=10
PHOENIXGUARD_FRAME_INGEST_MAX_ACTIVE_FEEDS_TOTAL=3
PHOENIXGUARD_FRAME_INGEST_MAX_ACTIVE_FEEDS_PER_TOKEN=1
PHOENIXGUARD_FRAME_INGEST_ALLOWED_ORIGINS=${ALLOWED_ORIGINS}
PHOENIXGUARD_ALLOWED_ORIGINS=${ALLOWED_ORIGINS}
PHOENIXGUARD_TRUSTED_HOSTS=${TRUSTED_HOSTS}
PHOENIXGUARD_ENABLE_OTEL=0
PHOENIXGUARD_RUNTIME_SINGLETON_DISABLE=0
EOF
chmod 0640 "${ENV_FILE}"
chown root:"${SERVICE_USER}" "${ENV_FILE}"

cat > /etc/systemd/system/phoenixguard-cloud-brain.service <<EOF
[Unit]
Description=PhoenixGuard Cloud Brain API and frame-ingest tracker
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${SERVICE_USER}
Group=${SERVICE_USER}
WorkingDirectory=${REPO_ROOT}
EnvironmentFile=${ENV_FILE}
ExecStart=${REPO_ROOT}/.venv-live/bin/python Backend/launch/start_phoenixguard_mobile_api.py
Restart=always
RestartSec=8
TimeoutStopSec=30
NoNewPrivileges=true
UMask=0077
LimitNOFILE=65535
PrivateTmp=true
ProtectSystem=full
ReadWritePaths=${REPO_ROOT}/runtime

[Install]
WantedBy=multi-user.target
EOF

cat > /etc/systemd/system/phoenixguard-cloud-watchdog.service <<EOF
[Unit]
Description=PhoenixGuard Cloud Brain watchdog
After=network-online.target phoenixguard-cloud-brain.service
Wants=network-online.target phoenixguard-cloud-brain.service

[Service]
Type=simple
User=root
WorkingDirectory=${REPO_ROOT}
EnvironmentFile=${ENV_FILE}
ExecStart=${REPO_ROOT}/.venv-live/bin/python Developer/deployment/phoenixguard_cloud_watchdog.py --base-url http://${API_HOST}:${API_PORT} --session-id ${SESSION_ID} --service-name phoenixguard-cloud-brain.service --log-path ${REPO_ROOT}/runtime/live/logs_live/cloud_watchdog.jsonl --interval-sec 30 --failure-threshold 3
Restart=always
RestartSec=10
NoNewPrivileges=true
UMask=0077

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable --now phoenixguard-cloud-brain.service
systemctl enable --now phoenixguard-cloud-watchdog.service

if [[ -n "${CLOUDFLARED_TOKEN}" ]]; then
  echo "[PhoenixGuard Cloud Brain] Installing Cloudflare Tunnel service."
  if ! command -v cloudflared >/dev/null 2>&1; then
    curl -L --output /usr/local/bin/cloudflared https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64
    chmod +x /usr/local/bin/cloudflared
  fi
  cloudflared service install "${CLOUDFLARED_TOKEN}"
  systemctl enable --now cloudflared
fi

echo "[PhoenixGuard Cloud Brain] Waiting for health endpoint."
api_healthy=0
for _ in $(seq 1 40); do
  if curl -fsS "http://${API_HOST}:${API_PORT}/v1/mobile/health" >/dev/null; then
    echo "[PhoenixGuard Cloud Brain] API is healthy."
    api_healthy=1
    break
  fi
  sleep 3
done
if [[ "${api_healthy}" != "1" ]]; then
  echo "[PhoenixGuard Cloud Brain] API did not become healthy." >&2
  journalctl -u phoenixguard-cloud-brain.service --no-pager -n 120 >&2 || true
  exit 1
fi

echo "[PhoenixGuard Cloud Brain] Checking frame-ingest readiness."
if ! curl -fsS "http://${API_HOST}:${API_PORT}/v1/mobile/frame-ingest/readiness" >/dev/null; then
  echo "[PhoenixGuard Cloud Brain] Frame ingest is not ready." >&2
  curl -sS "http://${API_HOST}:${API_PORT}/v1/mobile/frame-ingest/config" >&2 || true
  journalctl -u phoenixguard-cloud-brain.service --no-pager -n 120 >&2 || true
  exit 1
fi

echo ""
echo "PhoenixGuard cloud brain installed."
echo "Local health: http://${API_HOST}:${API_PORT}/v1/mobile/health"
if [[ -n "${DOMAIN}" ]]; then
  echo "Public URL should be: https://${DOMAIN}"
fi
echo "Frame ingest token:"
echo "${FRAME_INGEST_TOKEN}"
echo ""
echo "Store that token securely. Edge frame agents need it to feed the cloud brain."
echo "Token registry: ${FEED_TOKEN_REGISTRY}"
