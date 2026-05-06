# Huats Club 2026
#!/usr/bin/env python3
"""
BU03-Kit Anchor Calibration Helper
====================================
Measures per-anchor distance offsets so the viewer can correct readings.

Procedure (do this once for each anchor):
  1) Place ONE tag at a measured, known distance from one anchor.
     E.g., 1.000 m from anchor 0, with a tape measure to confirm.
  2) The other tags should be powered OFF (so we don't have to worry
     about which slot they're in).
  3) Run this script, telling it which anchor and the true distance.
  4) Hold the tag still for the capture duration.
  5) The script reports the offset to add to that anchor's distances.

Repeat for each anchor. Then put the offsets in your viewer's config.

Run:
    python3 viewer_calibrate.py --anchor 0 --true-distance 1.5 --seconds 20
"""

import argparse
import statistics
import struct
import sys
import time

import serial

SERIAL_PORT  = "/dev/serial0"
BAUD_RATE    = 115200
FRAME_HEADER = b"\xaa\x25\x01"
FRAME_SIZE   = 37
TRAILER      = 0x55


def find_frames(buf: bytearray) -> list[bytes]:
    frames = []
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
        candidate = bytes(buf[:FRAME_SIZE])
        if candidate[-1] == TRAILER:
            frames.append(candidate)
            del buf[:FRAME_SIZE]
        else:
            del buf[:1]
    return frames


def parse_distance(frame: bytes, anchor_id: int) -> float:
    off = 3 + anchor_id * 4
    (mm,) = struct.unpack_from("<I", frame, off)
    return mm / 1000.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--anchor", type=int, required=True,
                    help="Anchor ID (0, 1, or 2) being calibrated.")
    ap.add_argument("--true-distance", type=float, required=True,
                    help="Actual measured distance from tag to that anchor (m).")
    ap.add_argument("--seconds", type=float, default=15.0,
                    help="Capture duration (default 15s — keep tag still).")
    ap.add_argument("--n-tags", type=int, default=1,
                    help="Number of tags powered up (default 1; recommend 1).")
    args = ap.parse_args()

    if not 0 <= args.anchor <= 7:
        print("--anchor must be 0..7")
        sys.exit(1)

    print("=" * 60)
    print(f" Calibrating Anchor {args.anchor}")
    print("=" * 60)
    print(f" True distance:    {args.true_distance:.3f} m")
    print(f" Capture duration: {args.seconds:.1f} s")
    print(f" Tags powered:     {args.n_tags}")
    if args.n_tags > 1:
        print(" WARNING: with multiple tags, frames are time-multiplexed.")
        print("          Only every Nth frame will be the calibrating tag.")
    print()
    print(" Make sure ONE tag is held STILL at the measured distance.")
    print(" Capture starts in 3 seconds...")
    for i in (3, 2, 1):
        print(f"   {i}...")
        time.sleep(1)
    print(" GO\n")

    try:
        ser = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=0.1)
    except serial.SerialException as e:
        print(f"[ERROR] Could not open {SERIAL_PORT}: {e}")
        sys.exit(1)
    ser.reset_input_buffer()

    buf = bytearray()
    measurements = []
    start = time.time()
    last_print = start
    frame_idx = 0

    try:
        while time.time() - start < args.seconds:
            data = ser.read(256)
            if data:
                buf.extend(data)
            for raw in find_frames(buf):
                # If multiple tags, we only want the calibrating tag's frames.
                # Without knowing which slot it occupies, we accept everything
                # but caller is encouraged to use --n-tags 1.
                if args.n_tags > 1 and (frame_idx % args.n_tags) != 0:
                    frame_idx += 1
                    continue
                d = parse_distance(raw, args.anchor)
                if 0.05 < d < 50.0:
                    measurements.append(d)
                frame_idx += 1

            if time.time() - last_print > 1.0:
                last_print = time.time()
                if measurements:
                    mean = statistics.fmean(measurements)
                    print(f"   {len(measurements):4d} samples, "
                          f"running mean = {mean:.3f} m")
                else:
                    print(f"   ...waiting for valid frames...")
    except KeyboardInterrupt:
        print("\n  (stopped early)")
    finally:
        ser.close()

    if not measurements:
        print("\n[ERROR] No valid measurements captured. Check power, wiring, "
              "and that the tag is in range.")
        sys.exit(1)

    mean = statistics.fmean(measurements)
    median = statistics.median(measurements)
    stdev = statistics.stdev(measurements) if len(measurements) > 1 else 0.0
    offset = args.true_distance - mean

    print("\n" + "=" * 60)
    print(" RESULTS")
    print("=" * 60)
    print(f" Samples captured: {len(measurements)}")
    print(f" Measured mean:    {mean:.3f} m")
    print(f" Measured median:  {median:.3f} m")
    print(f" Measured stdev:   {stdev:.3f} m")
    print(f" True distance:    {args.true_distance:.3f} m")
    print(f"")
    print(f" -> Offset for anchor {args.anchor}: {offset:+.3f} m")
    print(f"    (add this value to anchor {args.anchor}'s readings to correct)")
    print()
    print(f" In your viewer config, add:")
    print(f"   ANCHOR_OFFSETS[{args.anchor}] = {offset:.3f}")
    print("=" * 60)


if __name__ == "__main__":
    main()
