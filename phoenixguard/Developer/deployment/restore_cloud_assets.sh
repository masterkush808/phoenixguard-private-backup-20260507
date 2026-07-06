#!/usr/bin/env bash
set -Eeuo pipefail

ARCHIVE_PATH="${1:-}"
REPO_ROOT="${REPO_ROOT:-/opt/phoenixguard/phoenixguard}"
SERVICE_USER="${SERVICE_USER:-phoenixguard}"

if [[ -z "${ARCHIVE_PATH}" ]]; then
  echo "Usage: sudo REPO_ROOT=/opt/phoenixguard/phoenixguard bash restore_cloud_assets.sh /path/to/assets.zip" >&2
  exit 2
fi

if [[ "$(id -u)" -ne 0 ]]; then
  echo "Run as root so ownership can be fixed: sudo bash restore_cloud_assets.sh ${ARCHIVE_PATH}" >&2
  exit 2
fi

if [[ ! -f "${ARCHIVE_PATH}" ]]; then
  echo "Asset archive not found: ${ARCHIVE_PATH}" >&2
  exit 2
fi

apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y unzip zstd

mkdir -p "${REPO_ROOT}"
if file "${ARCHIVE_PATH}" | grep -qi "zip"; then
  unzip -o "${ARCHIVE_PATH}" -d "${REPO_ROOT}"
else
  tar --zstd -xf "${ARCHIVE_PATH}" -C "${REPO_ROOT}"
fi

chown -R "${SERVICE_USER}:${SERVICE_USER}" \
  "${REPO_ROOT}/models" \
  "${REPO_ROOT}/memory_bank" \
  "${REPO_ROOT}/adapters" \
  "${REPO_ROOT}/808 Memory" \
  "${REPO_ROOT}/data" \
  "${REPO_ROOT}/yolov8n.pt" 2>/dev/null || true

systemctl restart phoenixguard-cloud-brain.service
systemctl --no-pager --full status phoenixguard-cloud-brain.service
