[![License: NCPUL](https://img.shields.io/badge/license-NCPUL-blue.svg)](./LICENSE.md)

# sysmon‑agent

Lightweight **system‑monitoring daemon** that streams your machine’s hardware details and live resource usage (CPU, RAM, disk) to a WebSocket endpoint at a configurable interval.
Runs as a self‑contained **systemd service** inside a Python virtual‑env and logs to `/var/log/sysmon‑agent/`.

---

## Features

* **Metrics** – sends one‑time hardware info plus periodic utilisation snapshots.
* **Configurable** – just edit `config.toml` (URL & interval).
* **Self‑installing** – `install.sh` sets up a venv, installs deps, writes a systemd unit, and starts the service.
* **Friendly wrapper** – `sysmon-agent` opens the config in `nano`, `sysmon-agent -l` tails the live log.
* **Clean removal** – `uninstall.sh` stops the service and deletes all files.

---

## Requirements

* Linux with **systemd**
* Root (sudo) access for install/uninstall scripts

---

## Installation

```bash
git clone https://github.com/yourname/sysmon-agent.git
cd sysmon-agent
sudo ./install.sh
```

The installer will:

1. Copy project files to `/opt/sysmon-agent/`.
2. Create a virtual‑env and install packages from `requirements.txt`.
3. Write `/etc/systemd/system/sysmon-agent.service`.
4. Enable & start the service.
5. Create `/usr/local/bin/sysmon-agent` helper.

> **Tip:** `install.sh` also installs `python3‑venv`, `pip`, and `systemd` via `apt` if missing.

---

## Configuration

A sample file is provided as **`config.example.toml`**:

```toml
# Unique identifier for this machine
system-identifier =  "Machine-1"
# WebSocket server URL
url               =  "wss://server.domain/ws"
# seconds (optional, default is 10 seconds)
interval          =  10 
```

After install the file is copied to `/opt/sysmon-agent/config.toml`.

```bash
# Edit config with nano
sysmon-agent

# or use your favourite editor
sudo nano /opt/sysmon-agent/config.toml
# Reload the service after changes
sudo systemctl restart sysmon-agent
```

---

## Viewing Logs

```bash
sysmon-agent -l              # live‑tail stdout log
# or
sudo journalctl -u sysmon-agent -f   # via systemd journal
```

Log files live in `/var/log/sysmon-agent/` (stdout.log & stderr.log).

---

## Service Control

```bash
sudo systemctl status  sysmon-agent
sudo systemctl restart sysmon-agent
sudo systemctl stop    sysmon-agent
```

---

## Uninstall

```bash
cd sysmon-agent
sudo ./uninstall.sh
```

The script stops and disables the service, removes its unit file, venv, logs, and wrapper command.
