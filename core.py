from __future__ import annotations

import asyncio
import json
import platform
import socket
import subprocess
import time
import traceback
import urllib.request
from pathlib import Path
from typing import Any, Optional, Tuple

import psutil
import websockets

# ---------------------------------------------------------------------------
# Optional toml parser (built‑in on 3.11+, fallback to tomli)
# ---------------------------------------------------------------------------
try:  # Python 3.11+
    import tomllib as _toml
except ModuleNotFoundError:  # noqa: WPS440 – fallback for pre‑3.11 interpreters
    try:
        import tomli as _toml  # type: ignore
    except ModuleNotFoundError:  # pragma: no cover – clearly instruct the user
        raise SystemExit(
            "Missing TOML parser: install Python ≥ 3.11 or `pip install tomli`"
        )

# ---------------------------------------------------------------------------
# Data‑collection helpers
# ---------------------------------------------------------------------------

def _bytes_to_gib(value: int) -> float:
    """Convert bytes to GiB with one decimal precision."""
    return round(value / (1024 ** 3), 1)


def _get_optional_method(method: callable, *args: tuple, **kwargs: dict) -> Any:  # noqa: D401
    """Safely call *method* and swallow *any* exception, returning *None*."""
    try:
        return method(*args, **kwargs)
    except Exception:  # pragma: no cover – print full traceback for debugging
        print(traceback.format_exc())
        return None


# ---------------------------------------------------------------------------
# New network helpers
# ---------------------------------------------------------------------------

def _get_local_ip() -> str | None:  # noqa: D401 – imperative mood OK
    """Return the primary local IPv4 address, if any, without raising."""
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            # Reaching out to a public IP avoids localhost results (no packets sent).
            sock.connect(("8.8.8.8", 80))
            return sock.getsockname()[0]
    except Exception:
        return None


def _get_public_ip() -> str | None:  # noqa: D401 – imperative mood OK
    """Return the public IPv4 address using ipify; swallow any error."""
    try:
        with urllib.request.urlopen("https://api.ipify.org?format=json", timeout=3) as resp:
            return json.load(resp).get("ip")
    except Exception:
        return None


def get_network_info() -> dict[str, Any]:  # noqa: D401 – imperative mood OK
    """Gather hostname, FQDN, local/public IPs, and interface addresses."""
    interfaces: dict[str, list[str]] = {}
    try:
        for if_name, addrs in psutil.net_if_addrs().items():
            interfaces[if_name] = [addr.address for addr in addrs if addr.family == socket.AF_INET]
    except Exception:
        interfaces = {}

    return {
        "hostname": _get_optional_method(socket.gethostname),
        "fqdn": _get_optional_method(socket.getfqdn),
        "local_ip": _get_local_ip(),
        "public_ip": _get_public_ip(),
        "interfaces": interfaces,
    }

# ---------------------------------------------------------------------------
# Original hardware + usage helpers (updated to include network)
# ---------------------------------------------------------------------------

def get_hardware_info() -> dict[str, Any]:  # noqa: D401 – imperative mood OK
    """Return static hardware/OS information, plus network basics."""
    return {
        "network": get_network_info(),
        "os": {
            "system": _get_optional_method(platform.system),
            "release": _get_optional_method(platform.release),
            "version": _get_optional_method(platform.version),
            "machine": _get_optional_method(platform.machine),
            "processor": _get_optional_method(platform.processor),
        },
        "cpu": {
            "physical_cores": _get_optional_method(psutil.cpu_count, logical=False),
            "logical_cores": _get_optional_method(psutil.cpu_count, logical=True),
            "max_frequency_mhz": getattr(_get_optional_method(psutil.cpu_freq), "max", None),
        },
        "mem_total_gib": _bytes_to_gib(
            getattr(_get_optional_method(psutil.virtual_memory), "total", 0)
        ),
        "disks": [
            {
                "device": getattr(part, "device", None),
                "mountpoint": getattr(part, "mountpoint", None),
                "fstype": getattr(part, "fstype", None),
                "total_gib": _bytes_to_gib(
                    getattr(
                        _get_optional_method(psutil.disk_usage, getattr(part, "mountpoint", "")),
                        "total",
                        0,
                    )
                ),
            }
            for part in _get_optional_method(psutil.disk_partitions, all=False) or []
        ],
    }


def get_usage_info() -> dict[str, Any]:  # noqa: D401 – imperative mood OK
    """Return live utilisation metrics, plus network snapshot."""
    vm = _get_optional_method(psutil.virtual_memory)
    usage: dict[str, Any] = {
        "cpu_pct": _get_optional_method(psutil.cpu_percent, interval=None),
        "mem_used_gib": _bytes_to_gib(getattr(vm, "used", 0)),
        "disks": [],
        "network": get_network_info(),  # include in every update
    }

    for part in _get_optional_method(psutil.disk_partitions, all=False) or []:
        du = _get_optional_method(psutil.disk_usage, getattr(part, "mountpoint", ""))
        usage["disks"].append(
            {
                "device": getattr(part, "device", None),
                "used_gib": _bytes_to_gib(getattr(du, "used", 0)),
            }
        )
    return usage

# ---------------------------------------------------------------------------
# service helpers
# ---------------------------------------------------------------------------

def get_os():
    return platform.system()

def ensure_platform_supported(func):
    def wrapper(*args, **kwargs):
        os_type = get_os()
        if os_type not in ["Linux", "Windows"]:
            raise NotImplementedError(f"{os_type} is not supported yet.")
        return func(*args, **kwargs)
    return wrapper

@ensure_platform_supported
def list_services() -> list[dict[str, str]]:
    os_type = get_os()
    services = []

    try:
        if os_type == "Linux":
            result = subprocess.run(
                ['systemctl', 'list-units', '--type=service', '--all', '--no-pager', '--no-legend'],
                capture_output=True, text=True, check=True
            )
            for line in result.stdout.strip().splitlines():
                parts = line.split(None, 4)
                if len(parts) >= 4:
                    services.append({
                        "name": parts[0],
                        "load": parts[1],
                        "active": parts[2],
                        "sub": parts[3],
                    })

        elif os_type == "Windows":
            result = subprocess.run(
                ['sc', 'query', 'type=', 'service', 'state=', 'all'],
                shell=True, capture_output=True, text=True, check=True
            )
            current_service = {}
            for line in result.stdout.strip().splitlines():
                line = line.strip()
                if line.startswith("SERVICE_NAME:"):
                    if current_service:
                        services.append(current_service)
                    current_service = {"name": line.split(":", 1)[1].strip()}
                elif "STATE" in line:
                    state = line.split(":")[-1].strip().split("  ")[-1]
                    current_service["state"] = state
            if current_service:
                services.append(current_service)

    except subprocess.CalledProcessError as e:
        print(f"Error listing services: {e}")

    return services

@ensure_platform_supported
def get_service_status(service_name: str) -> dict[str, Optional[str]]:
    os_type = get_os()
    result = {
        "name": service_name,
        "running": None,
        "status": None,
    }

    try:
        if os_type == "Linux":
            output = subprocess.check_output(
                ['systemctl', 'status', service_name, '--no-pager'],
                text=True, stderr=subprocess.STDOUT
            )
            result["status_message"] = output
            result["is_running"] = "Active: active (running)" in output

        elif os_type == "Windows":
            output = subprocess.check_output(
                ['sc', 'query', service_name],
                text=True, stderr=subprocess.STDOUT, shell=True
            )
            result["status_message"] = output
            result["is_running"] = "RUNNING" in output

    except subprocess.CalledProcessError as e:
        result["status_message"] = e.output
        result["is_running"] = False  # assume not running if call fails

    return result

@ensure_platform_supported
def restart_service(service_name: str) -> dict[str, str]:
    os_type = get_os()
    result = {
        "name": service_name,
        "success": False,
        "message": "",
    }

    try:
        if os_type == "Linux":
            subprocess.check_output(
                ['sudo', 'systemctl', 'restart', service_name],
                stderr=subprocess.STDOUT,
                text=True
            )
            result["success"] = True
            result["message"] = "Service restarted successfully."

        elif os_type == "Windows":
            subprocess.check_output(['sc', 'stop', service_name], stderr=subprocess.STDOUT, shell=True, text=True)
            subprocess.check_output(['sc', 'start', service_name], stderr=subprocess.STDOUT, shell=True, text=True)
            result["success"] = True
            result["message"] = "Service restarted successfully."

    except subprocess.CalledProcessError as e:
        result["message"] = f"Failed to restart service: {e.output}"

    return result


def get_watched_services_status() -> dict[str, Any]:
    """Return the status of watched services."""
    s = []
    for service in WATCH_SERVICES:
        try:
            status = get_service_status(service)
            s.append(status)
        except Exception as e:
            print(f"Error getting status for {service}: {e}")
            s.append({"name": service, "is_running": False, "status_message": str(e)})
    return s


# ---------------------------------------------------------------------------
# Configuration helper
# ---------------------------------------------------------------------------

def load_config() -> Tuple[str, str, float]:  # noqa: D401 – imperative mood OK
    """Parse **config.toml** in the script directory.

    Return *(system_identifier, url, interval)*. Failures are fatal.
    """

    cfg_path = Path(__file__).with_name("config.toml")
    default_interval = 10.0

    try:
        with cfg_path.open("rb") as fp:
            cfg = _toml.load(fp)

        system_identifier = str(cfg.get("system-identifier", "")).strip()
        if not system_identifier:
            raise SystemExit(
                f"Missing 'system-identifier' in {cfg_path}; please set it to a unique identifier for this machine."
            )

        url = str(cfg.get("url", "")).strip()
        if not url:
            raise SystemExit(
                f"Missing 'url' in {cfg_path}; please set it to your WebSocket server URL."
            )
        if not url.startswith("ws://") and not url.startswith("wss://"):
            raise SystemExit(
                f"Invalid 'url' in {cfg_path}; must start with 'ws://' or 'wss://'."
            )
        
        interval = float(cfg.get("interval", default_interval))
        if interval <= 0:
            raise SystemExit(
                f"Invalid 'interval' in {cfg_path}; must be a positive number."
            )
        return system_identifier, url, interval
    except Exception as exc:
        print(f"Error parsing {cfg_path}: {exc};")
        raise SystemExit(
            f"Please fix the configuration file at {cfg_path} and try again."
        ) from exc

# ---------------------------------------------------------------------------
# WebSocket Client
# ---------------------------------------------------------------------------

WATCH_SERVICES = []
SYSTEM_ID = None

async def transmit(uri: str, interval: float) -> None:  # noqa: D401 – imperative mood OK
    """Connect to *uri* and stream JSON-encoded stats every *interval* seconds."""

    reconnect_delay = min(max(interval, 1.0), 30.0)  # Clamp to [1, 30] seconds

    while True:
        try:
            async with websockets.connect(uri, ping_interval=None) as ws:
                print(f"✓ Connected to {uri}")

                await ws.send(json.dumps(
                    {
                        "system_id": SYSTEM_ID,
                        "timestamp": time.time(),
                        "type": "get_watch_services",
                    }
                ))

                payload = {
                    "system_id": SYSTEM_ID,
                    "timestamp": time.time(),
                    "type": "hardware_info",
                    "hardware": get_hardware_info(),
                }
                await ws.send(json.dumps(payload, separators=(",", ":")))

                # Create two tasks: sending and receiving
                send_task = asyncio.create_task(send_loop(ws, interval))
                receive_task = asyncio.create_task(receive_loop(ws))

                await asyncio.gather(send_task, receive_task)

        except (websockets.InvalidURI, websockets.InvalidHandshake) as cfg_err:
            raise SystemExit(f"WebSocket configuration error: {cfg_err}")
        except Exception as conn_err:  # covers disconnects, timeouts, etc.
            print(f"Connection lost ({conn_err!s}); retrying in {reconnect_delay}s …")
            await asyncio.sleep(reconnect_delay)

async def send_loop(ws, interval: float) -> None:
    while True:
        payload = {
            "system_id": SYSTEM_ID,
            "timestamp": time.time(),
            "type": "usage_info",
            "usage": get_usage_info(),
            "watched_services": get_watched_services_status(),
        }
        await ws.send(json.dumps(payload, separators=(",", ":")))
        await asyncio.sleep(interval)

async def receive_loop(ws) -> None:
    async for message in ws:
        await handle_message(ws, message)


async def handle_message(ws, message: str) -> None:
    """Process a received message from the WebSocket server."""
    try:
        data = json.loads(message)
        msg_type = data.get("type", "unknown")

        if msg_type == "get_services":
            # return the list of all services
            print("🔍 Request for all services received.")
            try:
                services = list_services()

                response = {
                    "system_id": SYSTEM_ID,
                    "timestamp": time.time(),
                    "type": "get_services",
                    "services": services,
                }

                await ws.send(json.dumps(response, separators=(",", ":")))
            except Exception as e:
                print(f"⚠️ Error listing services: {e}")
                await send_error(ws, "get_services", str(e))

        elif msg_type == "set_watch_services":
            global WATCH_SERVICES
            WATCH_SERVICES = data.get("services", [])
            print(f"📡 Watch services updated: {WATCH_SERVICES}")
            await send_success(ws, "set_watch_services")

        elif msg_type == "restart_service":
            service_name = data.get("service")
            if service_name:
                print(f"🔄 Restarting service: {service_name}")
                try:
                    restart_service(service_name)
                    await send_success(ws, "restart_service", f"Service {service_name} restarted successfully.")
                except Exception as e:
                    print(f"⚠️ Error restarting service {service_name}: {e}")
                    await send_error(ws, "restart_service", str(e))
            else:
                print(f"⚠️ Missing service name for restart: {data}")
                await send_error(ws, "restart_service", "Missing service name")

        else:
            print(f"Unknown message type: {data}")
            await send_error(ws, "unknown", "Unknown message type")
    except json.JSONDecodeError:
        print(f"⚠️ Failed to decode message: {message}")

async def send_error(ws, type: str, error_message: str) -> None:
    """Send an error message to the WebSocket server."""
    error_payload = {
        "system_id": SYSTEM_ID,
        "timestamp": time.time(),
        "type": type,
        "error": error_message,
    }
    await ws.send(json.dumps(error_payload, separators=(",", ":")))

async def send_success(ws, type: str, message: str = "") -> None:
    """Send a success message to the WebSocket server."""
    success_payload = {
        "system_id": SYSTEM_ID,
        "timestamp": time.time(),
        "type": type,
        "ok": message,
    }
    await ws.send(json.dumps(success_payload, separators=(",", ":")))

# ---------------------------------------------------------------------------
# CLI Entry‑Point
# ---------------------------------------------------------------------------

def main() -> None:  # noqa: D401 – imperative mood OK
    global SYSTEM_ID
    sys_id, url, interval = load_config()
    SYSTEM_ID = sys_id
    # sys_id, url, interval = "test", "ws://localhost:8765", 10.0
    print(f"Using system_id={sys_id!r}, url={url!r}, interval={interval}s from config.")
    try:
        asyncio.run(transmit(url, interval))
    except KeyboardInterrupt:
        print("\nInterrupted by user; exiting.")


if __name__ == "__main__":
    main()
