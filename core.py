from __future__ import annotations

import asyncio
import json
import platform
import socket
import time
import traceback
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


def get_hardware_info() -> dict[str, Any]:
    """Return static hardware/OS information."""
    return {
        "hostname": _get_optional_method(socket.gethostname),
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
        "memory": {
            "total_gib": _bytes_to_gib(
                getattr(_get_optional_method(psutil.virtual_memory), "total", 0)
            ),
        },
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
    """Return live utilisation metrics."""
    vm = _get_optional_method(psutil.virtual_memory)
    usage: dict[str, Any] = {
        "cpu_percent": _get_optional_method(psutil.cpu_percent, interval=None),
        "memory": {
            "used_gib": _bytes_to_gib(getattr(vm, "used", 0)),
            "available_gib": _bytes_to_gib(getattr(vm, "available", 0)),
            "percent": getattr(vm, "percent", 0),
        },
        "disks": [],
    }

    for part in _get_optional_method(psutil.disk_partitions, all=False) or []:
        du = _get_optional_method(psutil.disk_usage, getattr(part, "mountpoint", ""))
        usage["disks"].append(
            {
                "mountpoint": getattr(part, "mountpoint", None),
                "used_gib": _bytes_to_gib(getattr(du, "used", 0)),
                "free_gib": _bytes_to_gib(getattr(du, "free", 0)),
                "percent": getattr(du, "percent", 0),
            }
        )
    return usage

# ---------------------------------------------------------------------------
# Configuration helper
# ---------------------------------------------------------------------------

def load_config() -> Tuple[str, float]:  # noqa: D401 – imperative mood OK
    """Parse **config.toml** in the script directory and return *(url, interval)*.

    Failures are fatal; the monitor will not run.
    """

    cfg_path = Path(__file__).with_name("config.toml")
    default_interval = 10.0

    try:
        with cfg_path.open("rb") as fp:
            cfg = _toml.load(fp)
        url = str(cfg.get("url")).strip()
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
        return url, interval
    except Exception as exc:
        print(f"Error parsing {cfg_path}: {exc};")
        raise SystemExit(
            f"Please fix the configuration file at {cfg_path} and try again."
        ) from exc

# ---------------------------------------------------------------------------
# WebSocket Client
# ---------------------------------------------------------------------------

async def transmit(uri: str, interval: float) -> None:  # noqa: D401 – imperative mood OK
    """Connect to *uri* and stream JSON-encoded stats every *interval* seconds."""

    hardware = get_hardware_info()  # Gather once – doesn't change while running
    reconnect_delay = min(max(interval, 1.0), 30.0)  # Clamp to [1, 30] seconds

    while True:
        try:
            async with websockets.connect(uri, ping_interval=None) as ws:
                print(f"✓ Connected to {uri}")

                await ws.send(json.dumps({"hardware": hardware}, separators=(',', ':')))

                while True:
                    payload = {
                        "timestamp": time.time(),
                        "usage": get_usage_info(),
                    }
                    await ws.send(json.dumps(payload, separators=(',', ':')))
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
    url, interval = load_config()
    print(f"Using url={url!r}, interval={interval}s from config.")
    try:
        asyncio.run(transmit(url, interval))
    except KeyboardInterrupt:
        print("\nInterrupted by user; exiting.")


if __name__ == "__main__":
    main()
