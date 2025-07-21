from __future__ import annotations

import asyncio
import json
import platform
import socket
import time
import traceback
import urllib.request
from pathlib import Path
from typing import Any, Tuple

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

async def transmit(system_id: str, uri: str, interval: float) -> None:  # noqa: D401 – imperative mood OK
    """Connect to *uri* and stream JSON-encoded stats every *interval* seconds."""

    reconnect_delay = min(max(interval, 1.0), 30.0)  # Clamp to [1, 30] seconds

    while True:
        try:
            async with websockets.connect(uri, ping_interval=None) as ws:
                print(f"✓ Connected to {uri}")

                payload = {
                    "system_id": system_id,
                    "timestamp": time.time(),
                    "type": "hardware_info",
                    "hardware": get_hardware_info(),
                }
                await ws.send(json.dumps(payload, separators=(",", ":")))

                while True:
                    payload = {
                        "system_id": system_id,
                        "timestamp": time.time(),
                        "type": "usage_info",
                        "usage": get_usage_info(),
                    }
                    await ws.send(json.dumps(payload, separators=(",", ":")))
                    await asyncio.sleep(interval)
        except (websockets.InvalidURI, websockets.InvalidHandshake) as cfg_err:
            raise SystemExit(f"WebSocket configuration error: {cfg_err}")
        except Exception as conn_err:  # covers disconnects, timeouts, etc.
            print(f"Connection lost ({conn_err!s}); retrying in {reconnect_delay}s …")
            await asyncio.sleep(reconnect_delay)

# ---------------------------------------------------------------------------
# CLI Entry‑Point
# ---------------------------------------------------------------------------

def main() -> None:  # noqa: D401 – imperative mood OK
    # sys_id, url, interval = load_config()
    sys_id, url, interval = "test", "ws://localhost:8765", 10.0
    print(f"Using system_id={sys_id!r}, url={url!r}, interval={interval}s from config.")
    try:
        asyncio.run(transmit(sys_id, url, interval))
    except KeyboardInterrupt:
        print("\nInterrupted by user; exiting.")


if __name__ == "__main__":
    main()
