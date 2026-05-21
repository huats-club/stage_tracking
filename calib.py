#!/usr/bin/env python3
"""
BU03-Kit Anchor Offset Calibrator
=================================
Place ONE tag at a precisely known point, run this, and it prints a ready
to paste ANCHOR_OFFSETS dict.

For each anchor it computes:
    offset[i] = true_geometric_distance[i] - mean_raw_measured[i]

so that  raw_measured + offset  ==  true distance  at the calibration point.

Run (tag at room centre of a 1x1 m square):
    python3 calibrate.py --x 0.5 --y 0.5

Other options:
    --seconds 8     collect for 8 s (default 6)
    --port /dev/serial0

IMPORTANT: keep the tag perfectly still during collection.
Edit ANCHORS below to match viewer.py EXACTLY (physical antenna positions).
"""

import argparse
import math
import struct
import sys
import time

import serial

# ---- Must match viewer.py exactly (physical antenna positions, metres) ----
ANCHORS = {
    0: (0.0, 0.0),
    1: (0.0, 0.50),
    2: (0.0, 1.0),
    3: (1.0, 1.0),
    4: (1.0, 0.50),
    5: (1.0, 0.0),
}
# ---------------------------------------------------------------------------

SERIAL_PORT  = "/dev/serial0"
BAUD_RATE    = 115200
FRAME_HEADER = b"\xaa\x25\x01"
FRAME_SIZE   = 37
TRAILER      = 0x55
MAX_BUF      = 8192


def parse_frame(frame):
    if len(frame) != FRAME_SIZE:
        return None
    if frame[:3] != FRAME_HEADER or frame[-1] != TRAILER:
        return None
    out = []
    for i in range(8):
        (mm,) = struct.unpack_from("<I", frame, 3 + i * 4)
        out.append(mm / 1000.0)
    return out


def find_frames(buf):
    frames = []
    if len(buf) > MAX_BUF:
        del buf[:-2]
    while True:
        idx = buf.find(FRAME_HEADER)
        if idx < 0:
            if len(buf) > 2:
                del buf[:-2]
            break
        if idx > 0:
            del buf[:idx]
        if len(buf) < FRAME_SIZE:
            break
        cand = bytes(buf[:FRAME_SIZE])
        if cand[-1] == TRAILER:
            frames.append(cand)
            del buf[:FRAME_SIZE]
        else:
            del buf[:1]
    return frames


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--x", type=float, required=True,
                    help="Known tag X coordinate (metres).")
    ap.add_argument("--y", type=float, required=True,
                    help="Known tag Y coordinate (metres).")
    ap.add_argument("--seconds", type=float, default=6.0,
                    help="Collection time in seconds (default 6).")
    ap.add_argument("--port", type=str, default=SERIAL_PORT)
    args = ap.parse_args()

    ids = sorted(ANCHORS.keys())
    true_dist = {a: math.hypot(ANCHORS[a][0] - args.x,
                               ANCHORS[a][1] - args.y) for a in ids}

    print(f"Calibration point: ({args.x}, {args.y})")
    print("True geometric distances:")
    for a in ids:
        print(f"  A{a}: {true_dist[a]:.3f} m")
    print(f"\nCollecting RAW distances for {args.seconds:.0f} s -- "
          f"keep the tag STILL...\n")

    try:
        ser = serial.Serial(args.port, BAUD_RATE, timeout=0.1)
    except serial.SerialException as e:
        print(f"[ERROR] Could not open {args.port}: {e}")
        sys.exit(1)
    ser.reset_input_buffer()

    samples = {a: [] for a in ids}
    buf = bytearray()
    t_end = time.time() + args.seconds
    n_frames = 0

    while time.time() < t_end:
        data = ser.read(256)
        if data:
            buf.extend(data)
        for raw in find_frames(buf):
            d = parse_frame(raw)
            if d is None:
                continue
            n_frames += 1
            for a in ids:
                samples[a].append(d[a])  # RAW, no offsets
    ser.close()

    if n_frames < 5:
        print(f"[ERROR] Only {n_frames} frames collected -- check the tag "
              f"and serial link.")
        sys.exit(1)

    print(f"Collected {n_frames} frames.\n")
    print(f"{'A':>3} {'true':>8} {'raw_mean':>9} {'raw_sd':>8} "
          f"{'offset':>9} {'note':>14}")

    offsets = {}
    for a in ids:
        s = samples[a]
        mean = sum(s) / len(s)
        var = sum((v - mean) ** 2 for v in s) / len(s)
        sd = math.sqrt(var)
        off = true_dist[a] - mean
        offsets[a] = off
        note = ""
        if sd > 0.05:
            note = "NOISY"
        if abs(off) > 0.15:
            note = (note + " BIG-OFFSET").strip()
        if mean < 0.05:
            note = "NO-RANGE?"
        print(f"A{a:>2} {true_dist[a]:8.3f} {mean:9.3f} {sd:8.3f} "
              f"{off:+9.3f} {note:>14}")

    print("\n--- Paste this into viewer.py AND distcheck.py ---\n")
    print("ANCHOR_OFFSETS = {")
    for a in ids:
        print(f"    {a}: {offsets[a]:+.3f},")
    print("}")
    print()
    big = [a for a in ids if abs(offsets[a]) > 0.15]
    if big:
        print(f"NOTE: anchors {big} have large offsets (>15 cm). That usually "
              f"means a wrong\nanchor position or an antenna pointing the wrong "
              f"way -- worth checking the\nhardware before trusting these.")
    else:
        print("All offsets are small (<15 cm) -- looks healthy.")
    print("\nNext: re-run distcheck.py at a DIFFERENT known point to verify.")


if __name__ == "__main__":
    main()
