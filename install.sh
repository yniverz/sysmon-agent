#!/usr/bin/env bash
# Install the sysmon-agent service
set -euo pipefail

SERVICE_NAME="sysmon-agent"
INSTALL_DIR="/opt/${SERVICE_NAME}"
VENV_DIR="${INSTALL_DIR}/venv"
LOG_DIR="/var/log/${SERVICE_NAME}"
UNIT_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
CMD_WRAPPER="/usr/local/bin/${SERVICE_NAME}"
CONFIG_FILE="${INSTALL_DIR}/config.toml"

# Require root
if [[ $EUID -ne 0 ]]; then
  echo "Run as root (sudo)"; exit 1; fi

# Check required arguments
if [[ $# -ne 3 ]]; then
  echo "Usage: $0 <system_id> <url> <key>"
  exit 1
fi

SYSTEM_ID="$1"
URL="$2"
KEY="$3"

echo "▶ Creating folders …"
mkdir -p "${INSTALL_DIR}" "${LOG_DIR}"

echo "▶ Copying project files …"
install -m 644 core.py "${INSTALL_DIR}/"
install -m 644 requirements.txt "${INSTALL_DIR}/"

echo "▶ Creating config.toml with provided values …"
cat > "${CONFIG_FILE}" <<EOF
system-identifier = "${SYSTEM_ID}"
url = "${URL}"
key = "${KEY}"
EOF

echo "▶ Installing required packages …"
apt-get update
apt-get install -y python3 python3-venv python3-pip systemd

echo "▶ Creating virtual environment …"
python3 -m venv "${VENV_DIR}"
"${VENV_DIR}/bin/pip" install --upgrade pip
"${VENV_DIR}/bin/pip" install -r "${INSTALL_DIR}/requirements.txt"

echo "▶ Writing systemd unit …"
cat > "${UNIT_FILE}" <<EOF
[Unit]
Description=System Monitor WebSocket Agent
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=${INSTALL_DIR}
ExecStart=${VENV_DIR}/bin/python ${INSTALL_DIR}/core.py
Restart=always
RestartSec=10
StandardOutput=append:${LOG_DIR}/stdout.log
StandardError=append:${LOG_DIR}/stderr.log

[Install]
WantedBy=multi-user.target
EOF

echo "▶ Installing helper wrapper …"
cat > "${CMD_WRAPPER}" <<'EOS'
#!/usr/bin/env bash
# sysmon-agent helper: edit config or tail logs
CONFIG="/opt/sysmon-agent/config.toml"
LOGFILE="/var/log/sysmon-agent/stdout.log"
case "$1" in
  -l|--log) exec tail -f "$LOGFILE" ;;
  *)        exec nano "$CONFIG" ;;
esac
EOS
chmod +x "${CMD_WRAPPER}"

echo "▶ Enabling & starting service …"
systemctl daemon-reload
systemctl enable --now "${SERVICE_NAME}.service"

echo "✅ Installed. Use 'sysmon-agent' to edit the config or 'sysmon-agent -l' to watch logs."
