# Huats Club 2026 
#!/usr/bin/env python3
"""
BU03-Kit TWR Configuration & Live Distance Reader
==================================================
Configures any board in a multi-anchor + multi-tag TWR setup.

To configure each board, change ONE variable below (DEVICE) and run.
Repeat for every board.

Wiring (Pi <-> BU03-Kit):
    Pi 3.3V (pin 1)   -> BU03 3V3
    Pi GND  (pin 6)   -> BU03 GND
    Pi TXD  (pin 8)   -> BU03 RX1 (pin 7)   <-- AT command UART
    Pi RXD  (pin 10)  -> BU03 TX1 (pin 6)

Run:
    python3 bu03_config.py
"""

import serial
import time
import sys

# =============================================================================
# EDIT THIS SECTION ONLY
# =============================================================================

# --- Pick which board you are configuring right now ---
# Anchors:  "ANCHOR0", "ANCHOR1", "ANCHOR2"   (extend as needed)
# Tags:     "TAG0" .. "TAG9"   (firmware accepts IDs 0-9)
DEVICE = "ANCHOR3"

# --- Common UWB settings (must match across all boards) ---
CHANNEL = 1     # 1 = Channel 5 (6489.6 MHz)
RATE    = 1     # 1 = 6.8 Mbps
UWBMODE = 0     # 0 = TWR (Two-Way Ranging)

# --- Per-device config table ---
# Each entry: (id, role)   role: 0=TAG, 1=ANCHOR
DEVICE_TABLE = {
    # Anchors
    "ANCHOR0": (0, 1),
    "ANCHOR1": (1, 1),
    "ANCHOR2": (2, 1),
    "ANCHOR3": (3, 1),
    # Tags (IDs 0-9 confirmed accepted by firmware)
    "TAG0":    (0, 0),
    "TAG1":    (1, 0),
    "TAG2":    (2, 0),
    "TAG3":    (3, 0),
    "TAG4":    (4, 0),
    "TAG5":    (5, 0),
    "TAG6":    (6, 0),
    "TAG7":    (7, 0),
    "TAG8":    (8, 0),
    "TAG9":    (9, 0),
    # Backward-compatible alias
    "TAG":     (0, 0),
}

# --- Serial port settings ---
SERIAL_PORT = "/dev/serial0"
BAUD_RATE   = 115200
LINE_ENDING = "\r\n"            # CRLF — required by BU03 AT firmware

# --- Behaviour ---
WAIT_AFTER_CMD   = 1.0          # seconds to wait per command (relaxed; firmware
                                # prints a GBK status block after OK that needs
                                # time to drain)
INTER_CMD_PAUSE  = 0.3
RESTART_AFTER    = True
STREAM_DISTANCE  = True

# =============================================================================
# Below this line: usually no need to edit
# =============================================================================


def build_commands(device: str) -> list[str]:
    if device not in DEVICE_TABLE:
        print(f"[ERROR] Unknown DEVICE '{device}'. "
              f"Valid options: {sorted(DEVICE_TABLE.keys())}")
        sys.exit(1)
    dev_id, role = DEVICE_TABLE[device]
    return [
        "AT",
        f"AT+SETUWBMODE={UWBMODE}",
        f"AT+SETCFG={dev_id},{role},{CHANNEL},{RATE}",
        "AT+SAVE",
        "AT+GETCFG",
        "AT+GETUWBMODE",
    ]


def open_serial(port: str, baud: int) -> serial.Serial:
    try:
        ser = serial.Serial(
            port=port,
            baudrate=baud,
            bytesize=serial.EIGHTBITS,
            parity=serial.PARITY_NONE,
            stopbits=serial.STOPBITS_ONE,
            timeout=1.0,
            write_timeout=1.0,
        )
    except serial.SerialException as e:
        print(f"[ERROR] Could not open {port}: {e}")
        print("        Hints: is the cable seated? Is the port name correct?")
        print("        Try: ls -l /dev/serial0  /dev/ttyAMA*  /dev/ttyS0")
        sys.exit(1)
    ser.reset_input_buffer()
    ser.reset_output_buffer()
    time.sleep(0.1)
    return ser


def send_at(ser: serial.Serial, cmd: str, wait: float = WAIT_AFTER_CMD) -> str:
    payload = (cmd + LINE_ENDING).encode("ascii")
    ser.reset_input_buffer()
    ser.write(payload)
    ser.flush()

    deadline = time.time() + wait
    chunks = []
    while time.time() < deadline:
        n = ser.in_waiting
        if n:
            chunks.append(ser.read(n))
            deadline = max(deadline, time.time() + 0.3)
        else:
            time.sleep(0.02)

    raw = b"".join(chunks)
    try:
        return raw.decode("ascii", errors="replace").strip()
    except Exception:
        return repr(raw)


def configure(ser: serial.Serial, commands: list[str]) -> bool:
    dev_id, role = DEVICE_TABLE[DEVICE]
    role_name = "ANCHOR" if role == 1 else "TAG"
    print(f"\n=== Configuring {DEVICE} (ID={dev_id}, role={role_name}) "
          f"on {SERIAL_PORT} @ {BAUD_RATE} ===\n")
    all_ok = True
    for cmd in commands:
        print(f">>> {cmd}")
        resp = send_at(ser, cmd)
        if resp:
            for line in resp.splitlines():
                print(f"    {line}")
        else:
            print("    (no response)")
            all_ok = False
        time.sleep(INTER_CMD_PAUSE)
        print()
    return all_ok


def stream_output(ser: serial.Serial) -> None:
    """Show whatever the AT-command UART emits.

    Note: ranging distance frames come out the *data* UART (PA2/PA3),
    not the AT command UART (TX1/RX1) we're using here. So this stream
    is mostly for catching any stray ASCII status messages.
    """
    print(f"\n=== Streaming AT UART output from {DEVICE} (Ctrl+C to stop) ===\n")
    ser.timeout = 0.2
    buf = bytearray()
    try:
        while True:
            data = ser.read(256)
            if not data:
                continue
            buf.extend(data)
            while b"\n" in buf:
                line, _, rest = buf.partition(b"\n")
                buf = bytearray(rest)
                printable = sum(1 for b in line if 32 <= b < 127 or b in (9, 13))
                ts = time.strftime("%H:%M:%S")
                if line and printable / max(len(line), 1) > 0.8:
                    text = line.decode("ascii", errors="replace").rstrip("\r")
                    if text:
                        print(f"[{ts}] {text}")
                elif line:
                    hex_str = " ".join(f"{b:02x}" for b in line)
                    print(f"[{ts}] HEX: {hex_str}")
    except KeyboardInterrupt:
        print("\n(stopped)")


def main() -> None:
    commands = build_commands(DEVICE)
    ser = open_serial(SERIAL_PORT, BAUD_RATE)
    try:
        ok = configure(ser, commands)
        if not ok:
            print("[WARN] Some commands had no response. Check wiring "
                  "(TX<->RX swap), power, and the /dev/* port.")

        if RESTART_AFTER:
            print(">>> AT+RESTART  (applying saved config)")
            send_at(ser, "AT+RESTART", wait=2.0)
            time.sleep(1.5)
            ser.reset_input_buffer()

        if STREAM_DISTANCE:
            stream_output(ser)
    finally:
        ser.close()


if __name__ == "__main__":
    main()
