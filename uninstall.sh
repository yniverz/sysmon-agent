#!/usr/bin/env bash
# Remove the sysmon‑agent service and all related files
set -euo pipefail

SERVICE_NAME="sysmon-agent"
INSTALL_DIR="/opt/${SERVICE_NAME}"
LOG_DIR="/var/log/${SERVICE_NAME}"
UNIT_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
CMD_WRAPPER="/usr/local/bin/${SERVICE_NAME}"

if [[ $EUID -ne 0 ]]; then
  echo "Run as root (sudo)"; exit 1; fi

echo "▶ Stopping service …"
systemctl stop "${SERVICE_NAME}.service" || true
systemctl disable "${SERVICE_NAME}.service" || true

echo "▶ Removing unit file …"
rm -f "${UNIT_FILE}"
systemctl daemon-reload

echo "▶ Deleting application, logs & wrapper …"
rm -rf "${INSTALL_DIR}" "${LOG_DIR}" "${CMD_WRAPPER}"

echo "✅ Uninstalled."
