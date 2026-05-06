# Huats Club 2026
#!/usr/bin/env python3
"""
BU03-Kit Live Multi-Tag Visualizer (Tkinter version)
=====================================================
Real-time visualization of up to 8 tags using a Tkinter window
with an embedded matplotlib plot on top and a table with real
dropdown menus for color selection on the bottom.

Run:
    python3 viewer.py --tags 1 (To track number of tags) 
    python3 viewer.py --tags 2 --windowed (Disable Fullscreen)
"""

import argparse
import csv
import struct
import sys
import threading
import time
import tkinter as tk
from tkinter import ttk
from dataclasses import dataclass, field

import serial
import matplotlib
matplotlib.use("TkAgg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib.figure import Figure
from matplotlib.backends.backend_tkagg import FigureCanvasTkAgg

# To enter physical coordinate of anchor wrt to room
ANCHORS = {
    0: (0.0, 0.0),
    1: (1.8, 0.20),
    2: (1.8, -0.69),
    3: (0.0, -0.69),
}

# To enter calibrated value
ANCHOR_OFFSETS = {
    0: 0.0,
    1: 0.0,
    2: 0.0,
    3: 0.0,
}

# To enter plot limit
VIEW_BOUNDS = (0.0, 2.0, -1.0, 0.5)

TAG_COLORS = [
    "#ff5252", "#42a5f5", "#66bb6a", "#ffb74d",
    "#ab47bc", "#26a69a", "#ec407a", "#bdbdbd",
]

COLOR_NAMES = [
    "red", "blue", "green", "orange",
    "purple", "teal", "pink", "gray",
]

SERIAL_PORT  = "/dev/serial0"
BAUD_RATE    = 115200
FRAME_HEADER = b"\xaa\x25\x01"
FRAME_SIZE   = 37
TRAILER      = 0x55


def parse_frame(frame):
    if len(frame) != FRAME_SIZE:
        return None
    if frame[:3] != FRAME_HEADER or frame[-1] != TRAILER:
        return None
    distances = []
    for i in range(8):
        off = 3 + i * 4
        (mm,) = struct.unpack_from("<I", frame, off)
        distances.append(mm / 1000.0)
    return distances


def find_frames(buf):
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


def trilaterate_2d(anchor_positions, distances):
    valid = [(p[0], p[1], d) for p, d in zip(anchor_positions, distances)
             if p is not None and 0.05 < d < 50.0]
    if len(valid) < 3:
        return None
    x1, y1, r1 = valid[0]
    A, b = [], []
    for xi, yi, ri in valid[1:]:
        A.append([2 * (xi - x1), 2 * (yi - y1)])
        b.append(ri**2 - r1**2 - xi**2 + x1**2 - yi**2 + y1**2)
    if len(A) < 2:
        return None
    det = A[0][0] * A[1][1] - A[0][1] * A[1][0]
    if abs(det) < 1e-6:
        return None
    x = -(b[0] * A[1][1] - b[1] * A[0][1]) / det
    y = -(A[0][0] * b[1] - A[1][0] * b[0]) / det
    return x, y


class Kalman2D:
    def __init__(self, dt=0.10, q=0.12, r=1.1):
        self.dt = dt
        self.q = q
        self.r = r
        self.state = [0.0, 0.0, 0.0, 0.0]
        self.P = [[1.0, 0, 0, 0], [0, 1.0, 0, 0],
                  [0, 0, 1.0, 0], [0, 0, 0, 1.0]]
        self.initialized = False

    def predict(self):
        if not self.initialized:
            return
        self.state[0] += self.state[2] * self.dt
        self.state[1] += self.state[3] * self.dt
        for i in range(4):
            self.P[i][i] += self.q

    def update(self, mx, my):
        if not self.initialized:
            self.state = [mx, my, 0.0, 0.0]
            self.initialized = True
            return mx, my
        Kx = self.P[0][0] / (self.P[0][0] + self.r)
        Ky = self.P[1][1] / (self.P[1][1] + self.r)
        old_x, old_y = self.state[0], self.state[1]
        self.state[0] += Kx * (mx - self.state[0])
        self.state[1] += Ky * (my - self.state[1])
        self.state[2] = (self.state[0] - old_x) / self.dt
        self.state[3] = (self.state[1] - old_y) / self.dt
        self.P[0][0] *= (1 - Kx)
        self.P[1][1] *= (1 - Ky)
        return self.state[0], self.state[1]


@dataclass
class TagState:
    last_distances: list = field(default_factory=lambda: [0.0] * 8)
    raw_position: tuple = None
    filt_position: tuple = None
    last_update: float = 0.0
    kalman: Kalman2D = field(default_factory=Kalman2D)


class SharedState:
    def __init__(self, n_tags):
        self.n_tags = n_tags
        self.tags = [TagState() for _ in range(n_tags)]
        self.row_color_index = list(range(n_tags))
        self.lock = threading.Lock()
        self.frame_count = 0
        self.start_time = time.time()
        self.stop = False


def reader_thread(state, csv_writer=None):
    try:
        ser = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=0.1)
    except serial.SerialException as e:
        print(f"[ERROR] Could not open {SERIAL_PORT}: {e}")
        state.stop = True
        return
    ser.reset_input_buffer()

    n_anchors = len(ANCHORS)
    anchor_positions = [ANCHORS.get(i) for i in range(8)]
    buf = bytearray()

    while not state.stop:
        try:
            data = ser.read(256)
        except Exception as e:
            print(f"[reader error] {e}")
            break
        if data:
            buf.extend(data)

        for raw in find_frames(buf):
            distances = parse_frame(raw)
            if distances is None:
                continue
            for aid, off in ANCHOR_OFFSETS.items():
                if aid < len(distances):
                    distances[aid] = max(0.0, distances[aid] + off)

            row = state.frame_count % state.n_tags
            tag = state.tags[row]

            anchors_for_trilat = [anchor_positions[i] for i in range(n_anchors)]
            dists_for_trilat = distances[:n_anchors]
            raw_pos = trilaterate_2d(anchors_for_trilat, dists_for_trilat)

            with state.lock:
                tag.last_distances = distances
                tag.last_update = time.time()
                if raw_pos is not None:
                    tag.kalman.predict()
                    fx, fy = tag.kalman.update(raw_pos[0], raw_pos[1])
                    tag.raw_position = raw_pos
                    tag.filt_position = (fx, fy)
                else:
                    tag.kalman.predict()
                state.frame_count += 1
                csv_color_idx = state.row_color_index[row]

            if csv_writer is not None:
                row_data = [time.time(), row, COLOR_NAMES[csv_color_idx]]
                row_data += [f"{distances[i]:.3f}" for i in range(n_anchors)]
                if raw_pos is not None:
                    row_data += [f"{raw_pos[0]:.3f}", f"{raw_pos[1]:.3f}"]
                else:
                    row_data += ["", ""]
                if tag.filt_position is not None:
                    row_data += [f"{tag.filt_position[0]:.3f}",
                                 f"{tag.filt_position[1]:.3f}"]
                else:
                    row_data += ["", ""]
                csv_writer.writerow(row_data)

    ser.close()
    print("Reader thread exited")


class ViewerApp:
    def __init__(self, root, state, show_circles, fullscreen):
        self.root = root
        self.state = state
        self.show_circles = show_circles
        self.n_anchors = len(ANCHORS)

        root.title("BU03 Live Tracker")
        root.configure(bg="#000000")

        style = ttk.Style()
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass

        root.grid_rowconfigure(0, weight=5)
        root.grid_rowconfigure(1, weight=1)
        root.grid_columnconfigure(0, weight=1)

        # ------ Top frame: matplotlib plot ------
        plot_frame = tk.Frame(root, bg="#000000")
        plot_frame.grid(row=0, column=0, sticky="nsew")

        plt.style.use("dark_background")
        self.fig = Figure(figsize=(14, 8))
        self.fig.patch.set_facecolor("#000000")
        self.ax_plot = self.fig.add_subplot(111)

        x_min, x_max, y_min, y_max = VIEW_BOUNDS
        self.ax_plot.set_xlim(x_min, x_max)
        self.ax_plot.set_ylim(y_min, y_max)
        self.ax_plot.set_aspect("equal")
        self.ax_plot.set_xlabel("X (m)")
        self.ax_plot.set_ylabel("Y (m)")
        self.ax_plot.grid(True, alpha=0.2)
        self.ax_plot.set_title("UWB Live Tracker — press Q to quit")
        self.ax_plot.set_facecolor("#000000")

        for aid, (ax_x, ax_y) in ANCHORS.items():
            self.ax_plot.plot(ax_x, ax_y, marker="^", markersize=14,
                              color="#ffeb3b", markeredgecolor="white")
            self.ax_plot.annotate(f"A{aid}", (ax_x, ax_y),
                                  textcoords="offset points",
                                  xytext=(8, 8), color="#ffeb3b", fontsize=11)

        self.row_dots = []
        self.row_circles_per_anchor = [[None] * self.n_anchors
                                        for _ in range(state.n_tags)]
        for i in range(state.n_tags):
            dot, = self.ax_plot.plot([], [], marker="o", markersize=10,
                                      color=TAG_COLORS[i],
                                      markeredgecolor="white", linewidth=0)
            self.row_dots.append(dot)

        self.hud = self.ax_plot.text(
            0.02, 0.98, "", transform=self.ax_plot.transAxes,
            va="top", ha="left", color="white",
            fontsize=10, family="monospace",
            bbox=dict(facecolor="black", alpha=0.5, edgecolor="none"),
        )

        self.canvas = FigureCanvasTkAgg(self.fig, master=plot_frame)
        self.canvas.draw()
        self.canvas.get_tk_widget().pack(fill="both", expand=True)

        # ------ Bottom frame: table with dropdowns ------
        table_frame = tk.Frame(root, bg="#000000")
        table_frame.grid(row=1, column=0, sticky="nsew", padx=10, pady=10)

        headers = ["Tag ID", "X (m)", "Y (m)", "Color"]
        for col, label in enumerate(headers):
            lbl = tk.Label(
                table_frame, text=label,
                bg="#222222", fg="white",
                font=("Helvetica", 13, "bold"),
                padx=8, pady=6, relief="solid", borderwidth=1,
            )
            lbl.grid(row=0, column=col, sticky="nsew")

        for col in range(4):
            table_frame.grid_columnconfigure(col, weight=1, uniform="cols")

        self.id_labels = []
        self.x_labels = []
        self.y_labels = []
        self.color_combos = []
        self.color_swatches = []

        for r in range(state.n_tags):
            id_lbl = tk.Label(
                table_frame, text=f"T{r}",
                bg="#111111", fg=TAG_COLORS[r],
                font=("Helvetica", 14, "bold"),
                padx=8, pady=6, relief="solid", borderwidth=1,
            )
            id_lbl.grid(row=r + 1, column=0, sticky="nsew")
            self.id_labels.append(id_lbl)

            x_lbl = tk.Label(
                table_frame, text="—",
                bg="#111111", fg="white",
                font=("Courier", 13),
                padx=8, pady=6, relief="solid", borderwidth=1,
            )
            x_lbl.grid(row=r + 1, column=1, sticky="nsew")
            self.x_labels.append(x_lbl)

            y_lbl = tk.Label(
                table_frame, text="—",
                bg="#111111", fg="white",
                font=("Courier", 13),
                padx=8, pady=6, relief="solid", borderwidth=1,
            )
            y_lbl.grid(row=r + 1, column=2, sticky="nsew")
            self.y_labels.append(y_lbl)

            color_cell = tk.Frame(
                table_frame, bg="#111111",
                relief="solid", borderwidth=1,
            )
            color_cell.grid(row=r + 1, column=3, sticky="nsew")

            swatch = tk.Frame(
                color_cell, bg=TAG_COLORS[r],
                width=24, height=24,
            )
            swatch.pack(side="left", padx=8, pady=6)
            swatch.pack_propagate(False)
            self.color_swatches.append(swatch)

            combo = ttk.Combobox(
                color_cell, values=COLOR_NAMES,
                state="readonly", width=10,
                font=("Helvetica", 12),
            )
            combo.set(COLOR_NAMES[r])
            combo.pack(side="left", padx=4, pady=6)
            combo.bind(
                "<<ComboboxSelected>>",
                lambda event, row=r: self.on_color_changed(row),
            )
            self.color_combos.append(combo)

        root.bind("<KeyPress-q>", lambda e: self.shutdown())
        root.bind("<KeyPress-Q>", lambda e: self.shutdown())
        root.bind("<Escape>", lambda e: self.shutdown())
        root.protocol("WM_DELETE_WINDOW", self.shutdown)

        if fullscreen:
            try:
                root.attributes("-fullscreen", True)
            except tk.TclError as e:
                print(f"[warn] could not enter fullscreen: {e}")

        self.root.after(100, self.update_loop)

    def on_color_changed(self, row):
        chosen_name = self.color_combos[row].get()
        try:
            chosen_idx = COLOR_NAMES.index(chosen_name)
        except ValueError:
            return

        with self.state.lock:
            current = self.state.row_color_index[row]
            if chosen_idx == current:
                return
            other_row = None
            for r, c in enumerate(self.state.row_color_index):
                if r != row and c == chosen_idx:
                    other_row = r
                    break
            self.state.row_color_index[row] = chosen_idx
            if other_row is not None:
                self.state.row_color_index[other_row] = current
            snapshot = list(self.state.row_color_index)

        names = [COLOR_NAMES[i] for i in snapshot]
        if other_row is not None:
            print(f"Row {row} -> {chosen_name}; row {other_row} swapped to "
                  f"{names[other_row]}. Mapping: "
                  f"{', '.join(f'row{i}={n}' for i, n in enumerate(names))}")
        else:
            print(f"Row {row} -> {chosen_name}. Mapping: "
                  f"{', '.join(f'row{i}={n}' for i, n in enumerate(names))}")

        self.sync_color_widgets(snapshot)

    def sync_color_widgets(self, color_indices):
        for r, ci in enumerate(color_indices):
            self.color_combos[r].set(COLOR_NAMES[ci])
            self.color_swatches[r].configure(bg=TAG_COLORS[ci])
            self.id_labels[r].configure(fg=TAG_COLORS[ci])

    def update_loop(self):
        if self.state.stop:
            return

        with self.state.lock:
            snapshot = []
            for tag in self.state.tags:
                snapshot.append({
                    "filt": tag.filt_position,
                    "dists": list(tag.last_distances[:self.n_anchors]),
                    "last":  tag.last_update,
                })
            total = self.state.frame_count
            elapsed = time.time() - self.state.start_time
            color_indices = list(self.state.row_color_index)

        now = time.time()

        for row, snap in enumerate(snapshot):
            color_idx = color_indices[row]
            color = TAG_COLORS[color_idx]
            pos = snap["filt"]
            stale = (now - snap["last"] > 1.0) if snap["last"] else True

            self.row_dots[row].set_color(color)
            self.row_dots[row].set_markerfacecolor(color)
            if pos is not None and not stale:
                self.row_dots[row].set_data([pos[0]], [pos[1]])
            else:
                self.row_dots[row].set_data([], [])

            self.id_labels[row].configure(fg=color)
            if pos is not None and not stale:
                self.x_labels[row].configure(text=f"{pos[0]:.3f}")
                self.y_labels[row].configure(text=f"{pos[1]:.3f}")
            else:
                self.x_labels[row].configure(text="—")
                self.y_labels[row].configure(text="—")

            if self.show_circles:
                for aid in range(self.n_anchors):
                    old = self.row_circles_per_anchor[row][aid]
                    if old is not None:
                        old.remove()
                        self.row_circles_per_anchor[row][aid] = None
                    if stale:
                        continue
                    d = snap["dists"][aid] if aid < len(snap["dists"]) else 0
                    if d <= 0.05:
                        continue
                    cx, cy = ANCHORS[aid]
                    circ = mpatches.Circle((cx, cy), d, fill=False,
                                           color=color, alpha=0.25,
                                           linewidth=1)
                    self.ax_plot.add_patch(circ)
                    self.row_circles_per_anchor[row][aid] = circ

        for r, ci in enumerate(color_indices):
            current_bg = self.color_swatches[r].cget("bg")
            target_bg = TAG_COLORS[ci]
            if current_bg != target_bg:
                self.color_swatches[r].configure(bg=target_bg)
            current_combo = self.color_combos[r].get()
            if current_combo != COLOR_NAMES[ci]:
                self.color_combos[r].set(COLOR_NAMES[ci])

        rate = total / elapsed if elapsed > 0 else 0
        active = sum(1 for s in snapshot
                     if s["filt"] is not None
                     and now - s["last"] < 1.0)
        colors_str = " ".join(
            f"T{i}={COLOR_NAMES[color_indices[i]]}"
            for i in range(self.state.n_tags)
        )
        self.hud.set_text(
            f"frames: {total}\n"
            f"rate:   {rate:5.1f} Hz\n"
            f"active: {active}/{self.state.n_tags}\n"
            f"colors: {colors_str}"
        )

        self.canvas.draw_idle()
        self.root.after(66, self.update_loop)

    def shutdown(self):
        self.state.stop = True
        try:
            self.root.destroy()
        except tk.TclError:
            pass


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--tags", type=int, default=2,
                    help="Number of tags currently active (1..8). Default 2.")
    ap.add_argument("--csv", type=str, default=None,
                    help="If set, log per-frame data to this CSV file.")
    ap.add_argument("--no-circles", action="store_true",
                    help="Hide distance circles (faster rendering).")
    ap.add_argument("--windowed", action="store_true",
                    help="Don't enter fullscreen mode.")
    args = ap.parse_args()

    if not 1 <= args.tags <= 8:
        print("--tags must be between 1 and 8")
        sys.exit(1)

    state = SharedState(n_tags=args.tags)
    for tag in state.tags:
        tag.kalman.dt = 0.10

    csv_file = None
    csv_writer = None
    if args.csv:
        csv_file = open(args.csv, "w", newline="")
        csv_writer = csv.writer(csv_file)
        n_anchors = len(ANCHORS)
        header = ["timestamp", "tag_id", "color"]
        header += [f"d{i}_m" for i in range(n_anchors)]
        header += ["raw_x", "raw_y", "filt_x", "filt_y"]
        csv_writer.writerow(header)

    print(f"Starting tracker for {args.tags} tags")
    print(f"Anchors: {ANCHORS}")
    print(f"Anchor offsets: {ANCHOR_OFFSETS}")
    print(f"View bounds: {VIEW_BOUNDS}")
    if args.csv:
        print(f"Logging to: {args.csv}")
    print("Press Q in the window to quit.")
    print("Use the Color dropdown in each table row to reassign colors.\n")

    reader = threading.Thread(
        target=reader_thread,
        args=(state, csv_writer),
        daemon=True,
    )
    reader.start()

    root = tk.Tk()
    app = ViewerApp(root, state,
                    show_circles=not args.no_circles,
                    fullscreen=not args.windowed)

    try:
        root.mainloop()
    except KeyboardInterrupt:
        pass
    finally:
        state.stop = True
        time.sleep(0.5)
        if csv_file:
            csv_file.close()
            print(f"Wrote {args.csv}")


if __name__ == "__main__":
    main()
