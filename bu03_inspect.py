# Huats Club 2026 
#!/usr/bin/env python3
"""
BU03-Kit Configuration Inspector (read-only)
=============================================
Queries the currently connected BU03-Kit and prints a human-readable
summary of its saved configuration. Does NOT modify anything.

Usage:
    python3 bu03_inspect.py
    python3 bu03_inspect.py --json     # machine-readable output
    python3 bu03_inspect.py --raw      # also show raw firmware responses
"""

import argparse
import json
import re
import serial
import sys
import time

SERIAL_PORT = "/dev/serial0"
BAUD_RATE   = 115200
LINE_ENDING = "\r\n"


# --- decoders for the firmware response strings ---

ROLE_MAP = {0: "TAG", 1: "ANCHOR"}
CHANNEL_MAP = {0: "Channel 9 (7987.2 MHz)", 1: "Channel 5 (6489.6 MHz)"}
RATE_MAP = {1: "6.8 Mbps"}
UWBMODE_MAP = {0: "TWR (Two-Way Ranging)", 1: "PDOA"}


def open_serial() -> serial.Serial:
    try:
        ser = serial.Serial(
            SERIAL_PORT, BAUD_RATE,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=1.0,
        )
    except serial.SerialException as e:
        print(f"[ERROR] Could not open {SERIAL_PORT}: {e}", file=sys.stderr)
        sys.exit(1)
    ser.reset_input_buffer()
    ser.reset_output_buffer()
    time.sleep(0.1)
    return ser


def send(ser: serial.Serial, cmd: str, wait: float = 0.5) -> str:
    """Send AT command, return raw response as string (ASCII, garbage-tolerant)."""
    ser.reset_input_buffer()
    ser.write((cmd + LINE_ENDING).encode("ascii"))
    ser.flush()

    deadline = time.time() + wait
    chunks = []
    while time.time() < deadline:
        n = ser.in_waiting
        if n:
            chunks.append(ser.read(n))
            deadline = max(deadline, time.time() + 0.1)
        else:
            time.sleep(0.02)
    return b"".join(chunks).decode("ascii", errors="replace").strip()


def parse_getcfg(raw: str) -> dict | None:
    """
    Expected line: 'getcfg ID:0, Role:1, CH:1, Rate:1'  (4-arg firmware)
    or            'getcfg ID:0, Role:1, CH:1, Rate:1, Group:1'  (5-arg firmware)
    """
    m = re.search(
        r"ID:\s*(\d+).*?Role:\s*(\d+).*?CH:\s*(\d+).*?Rate:\s*(\d+)(?:.*?Group:\s*(\d+))?",
        raw, re.IGNORECASE,
    )
    if not m:
        return None
    cfg = {
        "id":    int(m.group(1)),
        "role":  int(m.group(2)),
        "ch":    int(m.group(3)),
        "rate":  int(m.group(4)),
    }
    if m.group(5) is not None:
        cfg["group"] = int(m.group(5))
    return cfg


def parse_uwbmode(raw: str) -> int | None:
    m = re.search(r"twr_pdoa_mode:\s*(\d+)", raw, re.IGNORECASE)
    return int(m.group(1)) if m else None


def parse_version(raw: str) -> str | None:
    m = re.search(r"software:\s*(\S+)", raw, re.IGNORECASE)
    return m.group(1) if m else None


def query_all(ser: serial.Serial) -> dict:
    """Run the read-only AT queries and return a structured dict."""
    result = {"link_ok": False, "version": None, "cfg": None, "uwb_mode": None, "raw": {}}

    raw_at = send(ser, "AT")
    result["raw"]["AT"] = raw_at
    if "OK" not in raw_at.upper():
        return result
    result["link_ok"] = True

    raw_ver = send(ser, "AT+GETVER")
    result["raw"]["AT+GETVER"] = raw_ver
    result["version"] = parse_version(raw_ver)

    raw_cfg = send(ser, "AT+GETCFG", wait=0.8)
    result["raw"]["AT+GETCFG"] = raw_cfg
    result["cfg"] = parse_getcfg(raw_cfg)

    raw_mode = send(ser, "AT+GETUWBMODE", wait=0.8)
    result["raw"]["AT+GETUWBMODE"] = raw_mode
    result["uwb_mode"] = parse_uwbmode(raw_mode)

    return result


def pretty_print(data: dict, show_raw: bool = False) -> None:
    print("=" * 50)
    print(" BU03-Kit Configuration")
    print("=" * 50)

    if not data["link_ok"]:
        print(" Link:           NOT RESPONDING")
        print(" (No 'OK' from AT — check wiring, power, port.)")
        return
    print(" Link:           OK")

    if data["version"]:
        print(f" Firmware:       {data['version']}")
    else:
        print(" Firmware:       (could not parse)")

    cfg = data["cfg"]
    if cfg is None:
        print(" Config:         (could not parse AT+GETCFG response)")
    else:
        role_name = ROLE_MAP.get(cfg["role"], f"Unknown ({cfg['role']})")
        ch_name   = CHANNEL_MAP.get(cfg["ch"],  f"Unknown ({cfg['ch']})")
        rate_name = RATE_MAP.get(cfg["rate"],   f"Unknown ({cfg['rate']})")
        print(f" Device ID:      {cfg['id']}")
        print(f" Role:           {role_name}  (raw: {cfg['role']})")
        print(f" Channel:        {ch_name}  (raw: {cfg['ch']})")
        print(f" Data rate:      {rate_name}  (raw: {cfg['rate']})")
        if "group" in cfg:
            print(f" Group:          {cfg['group']}")

    mode = data["uwb_mode"]
    if mode is None:
        print(" UWB mode:       (could not parse)")
    else:
        mode_name = UWBMODE_MAP.get(mode, f"Unknown ({mode})")
        print(f" UWB mode:       {mode_name}  (raw: {mode})")

    print("=" * 50)

    if show_raw:
        print("\n--- Raw responses ---")
        for cmd, raw in data["raw"].items():
            print(f"\n>>> {cmd}")
            for line in raw.splitlines():
                print(f"    {line}")


def main():
    ap = argparse.ArgumentParser(description="Read BU03-Kit configuration (read-only).")
    ap.add_argument("--json", action="store_true", help="Output as JSON.")
    ap.add_argument("--raw",  action="store_true", help="Also show raw firmware responses.")
    args = ap.parse_args()

    ser = open_serial()
    try:
        data = query_all(ser)
    finally:
        ser.close()

    if args.json:
        print(json.dumps(data, indent=2))
    else:
        pretty_print(data, show_raw=args.raw)


if __name__ == "__main__":
    main()
