"""
Panorama Booth — single file, Tkinter UI + camera logic
Run with:  python3 panorama_ui.py
No extra installs needed — Tkinter is built into Python.
"""

import sys
import os
import shutil
import glob
import subprocess
import time
import threading
from datetime import datetime
import tkinter as tk
from tkinter import ttk
from PIL import Image, ImageTk, ImageEnhance, ExifTags

# ── Hardware imports ──────────────────────────────────────────────────────────
try:
    import pigpio
    import cv2
    import numpy as np
    import imutils
    from picamera2 import Picamera2
    from escpos.printer import Usb
    HARDWARE_AVAILABLE = True
except ImportError:
    HARDWARE_AVAILABLE = False


# ============================================================
# PIN CONFIGURATION
# ============================================================
SERVO_PIN = 26

# ============================================================
# FOLDER CONFIGURATION
# ============================================================
BASE_DIR            = os.path.dirname(os.path.abspath(__file__))
SAVE_DIR            = os.path.expanduser("~/photos")
IMAGE_FOLDER        = os.path.join(BASE_DIR, "imageprinter")
UNSTITCHED_FOLDER   = os.path.join(IMAGE_FOLDER, "unstitchedImages")
STITCHED_OUTPUT     = os.path.join(IMAGE_FOLDER, "stitchedOutputProcessed.png")
RAW_STITCHED_OUTPUT = os.path.join(IMAGE_FOLDER, "stitchedOutputRaw.png")

# ============================================================
# LOGO CONFIGURATION
# ============================================================
LOGO_PATH                   = os.path.join(BASE_DIR, "duen_logo.png")
PRINT_LOGO_AT_END           = True
ROTATE_LOGO_VERTICAL        = False
LOGO_ROTATION_DEGREES       = 270
LOGO_VERTICAL_WIDTH_SCALE   = 0.55
LOGO_HORIZONTAL_WIDTH_SCALE = 0.50
LOGO_PADDING_PX             = 40

# ============================================================
# SPEED + QUALITY SETTINGS
# ============================================================
STITCH_RESIZE_WIDTH      = 1600
FAST_SERVO_WAIT          = 0.28
FIRST_CAPTURE_WAIT       = 1.00
FIRST_CAPTURE_FLUSH_WAIT = 0.20
POST_CAPTURE_WAIT        = 0.20
IMAGE_WIDTH              = 1920
IMAGE_HEIGHT             = 1920
EXPOSURE_TIME_US         = 8000
ANALOGUE_GAIN            = 4.0

# ============================================================
# THERMAL PRINTER IMAGE SETTINGS
# ============================================================
PRINTER_WIDTH       = 384
WHITE_MAX_LEVEL     = 0.88
HIGHLIGHT_STRENGTH  = 0.18
PRINT_GAMMA         = 0.85
CLAHE_CLIP_LIMIT    = 2.0
BRIGHT_NOISE_AMOUNT = 10

# ============================================================
# ANGLE CONFIGURATION
# ============================================================
ANGLES_TO_CAPTURE = [
    265, 247, 229, 211, 193, 175, 157, 139,
    121, 103, 88, 73, 58, 43, 28, 13
]
MIN_PULSE   = 500
MAX_PULSE   = 2500
MAX_DEGREES = 270

# ============================================================
# GLOBALS
# ============================================================
pi     = None
camera = None

os.makedirs(SAVE_DIR, exist_ok=True)


# ============================================================
# HARDWARE INIT / SHUTDOWN
# ============================================================
def init_hardware():
    global pi, camera
    if not HARDWARE_AVAILABLE:
        return False
    try:
        pi = pigpio.pi()
        if not pi.connected:
            return False
        camera = Picamera2()
        camera.configure(camera.create_still_configuration(
            main={"size": (IMAGE_WIDTH, IMAGE_HEIGHT)}
        ))
        camera.start()
        time.sleep(4)
        return True
    except Exception as e:
        print(f"Hardware init failed: {e}")
        return False


def shutdown_hardware():
    try:
        camera.stop()
    except Exception:
        pass
    try:
        pi.set_servo_pulsewidth(SERVO_PIN, 0)
        pi.stop()
    except Exception:
        pass


# ============================================================
# SERVO
# ============================================================
def angle_to_pulse(angle):
    angle = max(0, min(MAX_DEGREES, angle))
    return int(MIN_PULSE + (angle / MAX_DEGREES) * (MAX_PULSE - MIN_PULSE))

def move_servo(angle):
    if pi and pi.connected:
        pi.set_servo_pulsewidth(SERVO_PIN, angle_to_pulse(angle))


# ============================================================
# IMAGE CAPTURE
# ============================================================
def setup_image_folders():
    if os.path.exists(IMAGE_FOLDER):
        shutil.rmtree(IMAGE_FOLDER)
    os.makedirs(UNSTITCHED_FOLDER, exist_ok=True)

def safe_capture_metadata():
    try:
        return camera.capture_metadata()
    except Exception as e:
        print(f"Metadata read failed: {e}")
        time.sleep(0.3)
        return camera.capture_metadata()

def warmup_camera():
    move_servo(135)
    time.sleep(0.7)
    camera.set_controls({"AeEnable": True, "AwbEnable": True})
    for _ in range(5):
        time.sleep(0.25)
        safe_capture_metadata()
    metadata = safe_capture_metadata()
    camera.set_controls({
        "AeEnable":     False,
        "ExposureTime": metadata.get("ExposureTime", EXPOSURE_TIME_US),
        "AnalogueGain": metadata.get("AnalogueGain", ANALOGUE_GAIN),
        "AwbEnable":    True,
    })
    time.sleep(0.4)

def flush_camera_once():
    temp = os.path.join(UNSTITCHED_FOLDER, "_throwaway.jpg")
    try:
        camera.capture_file(temp)
        if os.path.exists(temp):
            os.remove(temp)
        time.sleep(FIRST_CAPTURE_FLUSH_WAIT)
    except Exception as e:
        print(f"Throwaway frame failed: {e}")

def take_picture(angle, index):
    filename = os.path.join(UNSTITCHED_FOLDER, f"{index:02d}_angle_{angle:03d}.jpg")
    try:
        camera.capture_file(filename)
        time.sleep(POST_CAPTURE_WAIT)
        return filename
    except Exception as e:
        print(f"Capture failed at {angle}: {e}")
        time.sleep(0.5)
        camera.capture_file(filename)
        time.sleep(POST_CAPTURE_WAIT)
        return filename

def capture_still_for_feed():
    path = os.path.join(SAVE_DIR, f"feed_{datetime.now().strftime('%H%M%S')}.jpg")
    camera.capture_file(path)
    return path


# ============================================================
# PANORAMA STITCHING
# ============================================================
def extract_capture_index(path):
    return int(os.path.basename(path).split("_")[0])

def crop_only_outer_black(img):
    gray    = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    mask    = (gray > 10).astype(np.uint8)
    if mask.sum() == 0:
        return img
    rows, cols = mask.shape
    heights    = np.zeros(cols, dtype=np.int32)
    best_area  = 0
    best_rect  = (0, 0, rows, cols)
    for row in range(rows):
        heights = np.where(mask[row] == 1, heights + 1, 0)
        stack, i = [], 0
        while i <= cols:
            h = heights[i] if i < cols else 0
            if not stack or h >= heights[stack[-1]]:
                stack.append(i); i += 1
            else:
                top    = stack.pop()
                width  = i if not stack else i - stack[-1] - 1
                area   = width * heights[top]
                if area > best_area:
                    best_area = area
                    x1 = (i - width) if not stack else stack[-1] + 1
                    x2 = x1 + width
                    y2 = row + 1
                    y1 = y2 - heights[top]
                    best_rect = (y1, x1, y2, x2)
    y1, x1, y2, x2 = best_rect
    return img[y1:y2, x1:x2]

def stitch_images():
    paths  = sorted(glob.glob(os.path.join(UNSTITCHED_FOLDER, "*.jpg")), key=extract_capture_index)
    images = [imutils.resize(cv2.imread(p), width=STITCH_RESIZE_WIDTH) for p in paths if cv2.imread(p) is not None]
    if len(images) < 2:
        return False
    s = cv2.Stitcher_create(cv2.Stitcher_PANORAMA)
    s.setRegistrationResol(0.8)
    s.setSeamEstimationResol(0.1)
    s.setCompositingResol(-1)
    try:
        status, stitched = s.stitch(images)
    except cv2.error as e:
        print(f"Stitch error: {e}")
        return False
    if status == cv2.Stitcher_OK:
        cv2.imwrite(RAW_STITCHED_OUTPUT, stitched)
        stitched = cv2.copyMakeBorder(stitched, 10, 10, 10, 10, cv2.BORDER_CONSTANT, (0, 0, 0))
        cv2.imwrite(STITCHED_OUTPUT, crop_only_outer_black(stitched))
        return True
    return False


# ============================================================
# THERMAL PRINTING
# ============================================================
def make_thermal_print_image(img):
    img  = img.convert("L").rotate(90, expand=True)
    r    = PRINTER_WIDTH / img.width
    img  = img.resize((PRINTER_WIDTH, int(img.height * r)), Image.LANCZOS)
    arr  = np.array(img).astype(np.float32) / 255.0
    arr  = np.power(arr, PRINT_GAMMA)
    hi   = arr > 0.78
    arr[hi] = arr[hi] - HIGHLIGHT_STRENGTH * (arr[hi] - 0.78)
    arr  = np.clip(arr, 0.0, WHITE_MAX_LEVEL)
    a8   = (arr * 255).astype(np.uint8)
    a8   = cv2.createCLAHE(clipLimit=CLAHE_CLIP_LIMIT, tileGridSize=(8, 8)).apply(a8)
    blur = cv2.GaussianBlur(a8, (0, 0), 1.0)
    a8   = cv2.addWeighted(a8, 1.45, blur, -0.45, 0)
    af   = a8.astype(np.float32)
    bm   = af > 205
    af[bm] = af[bm] + np.random.normal(0, BRIGHT_NOISE_AMOUNT, af.shape)[bm]
    af   = np.clip(af, 0, 235)
    out  = Image.fromarray(af.astype(np.uint8)).convert("L")
    return out.convert("1", dither=Image.Dither.FLOYDSTEINBERG)

def make_logo_print_image(filepath):
    logo = Image.open(filepath).convert("L")
    if ROTATE_LOGO_VERTICAL:
        logo = logo.rotate(LOGO_ROTATION_DEGREES, expand=True)
        tw   = int(PRINTER_WIDTH * LOGO_VERTICAL_WIDTH_SCALE)
    else:
        tw   = int(PRINTER_WIDTH * LOGO_HORIZONTAL_WIDTH_SCALE)
    th   = int(logo.height * (tw / logo.width))
    logo = logo.resize((tw, th), Image.LANCZOS)
    canvas = Image.new("L", (PRINTER_WIDTH, th + LOGO_PADDING_PX * 2), 255)
    canvas.paste(logo, ((PRINTER_WIDTH - tw) // 2, LOGO_PADDING_PX))
    logo = ImageEnhance.Sharpness(ImageEnhance.Contrast(canvas).enhance(2.5)).enhance(2.0)
    logo = logo.convert("1", dither=Image.Dither.NONE)
    path = os.path.join(SAVE_DIR, f"logo_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png")
    logo.save(path)
    return path

def process_and_print(filepath):
    img = Image.open(filepath)
    try:
        exif = img._getexif()
        if exif:
            for tag, value in exif.items():
                if ExifTags.TAGS.get(tag) == "Orientation":
                    if value == 3:   img = img.rotate(180, expand=True)
                    elif value == 6: img = img.rotate(270, expand=True)
                    elif value == 8: img = img.rotate(90,  expand=True)
                    break
    except Exception:
        pass
    final = make_thermal_print_image(img)
    tmp   = os.path.join(SAVE_DIR, f"print_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png")
    final.save(tmp)
    logo_path = make_logo_print_image(LOGO_PATH) if PRINT_LOGO_AT_END and os.path.exists(LOGO_PATH) else None
    p = Usb(0x0485, 0x5741)
    p.hw("INIT")
    if logo_path: p.image(logo_path)
    p.image(tmp)
    if logo_path: p.image(logo_path)
    p.cut()


# ============================================================
# UI
# ============================================================
BG      = "#111111"
SURFACE = "#1e1e1e"
BORDER  = "#2e2e2e"
ACCENT  = "#7c6af7"
TEXT_HI = "#f0f0f0"
TEXT_LO = "#888888"
GREEN   = "#4caf80"
RED     = "#e05555"


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Panorama Booth")
        self.configure(bg=BG)
        self.geometry("700x860")
        self.resizable(True, True)

        self._worker     = None
        self._pano_done  = False
        self._feed_photo = None  # keep reference to avoid GC

        self._build_ui()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ── Layout ────────────────────────────────────────────────────────────
    def _build_ui(self):
        pad = dict(padx=16, pady=8)

        # Feed
        feed_frame = self._section("Camera feed")
        feed_frame.pack(fill="x", padx=16, pady=(16, 0))

        hdr = tk.Frame(feed_frame, bg=SURFACE)
        hdr.pack(fill="x", padx=12, pady=(8, 4))
        self._ts_lbl = tk.Label(hdr, text="—", bg=SURFACE, fg=TEXT_LO, font=("Courier New", 11))
        self._ts_lbl.pack(side="left")
        self._refresh_btn = self._btn(hdr, "⟳  Refresh", self._snap)
        self._refresh_btn.pack(side="right")

        self._feed_lbl = tk.Label(
            feed_frame, text="Press Refresh to capture a still",
            bg="#080808", fg=TEXT_LO, font=("Helvetica", 12),
            width=60, height=16, anchor="center"
        )
        self._feed_lbl.pack(fill="x", padx=12, pady=(0, 12))

        # Capture
        cap_frame = self._section("Capture")
        cap_frame.pack(fill="x", padx=16, pady=(12, 0))

        status_row = tk.Frame(cap_frame, bg=SURFACE)
        status_row.pack(fill="x", padx=12, pady=(8, 4))
        self._dot = tk.Label(status_row, text="●", bg=SURFACE, fg=TEXT_LO, font=("Helvetica", 10))
        self._dot.pack(side="left")
        self._status_lbl = tk.Label(
            status_row, text=f"Ready — {len(ANGLES_TO_CAPTURE)} angles configured",
            bg=SURFACE, fg=TEXT_LO, font=("Helvetica", 12)
        )
        self._status_lbl.pack(side="left", padx=(6, 0))

        # Angle pips
        pip_row = tk.Frame(cap_frame, bg=SURFACE)
        pip_row.pack(fill="x", padx=12, pady=4)
        self._pips = []
        for _ in ANGLES_TO_CAPTURE:
            c = tk.Canvas(pip_row, width=18, height=18, bg=SURFACE, highlightthickness=0)
            c.pack(side="left", padx=2)
            rect = c.create_rectangle(1, 1, 17, 17, fill=BORDER, outline="")
            self._pips.append((c, rect))

        # Progress bar (using ttk for the fill effect)
        style = ttk.Style()
        style.theme_use("default")
        style.configure("Pano.Horizontal.TProgressbar",
                        troughcolor=BORDER, background=ACCENT, thickness=4)
        self._pbar = ttk.Progressbar(
            cap_frame, style="Pano.Horizontal.TProgressbar",
            maximum=len(ANGLES_TO_CAPTURE), value=0
        )
        self._pbar.pack(fill="x", padx=12, pady=4)

        btn_row = tk.Frame(cap_frame, bg=SURFACE)
        btn_row.pack(fill="x", padx=12, pady=(4, 12))
        self._start_btn = self._btn(btn_row, "▶  Start panorama", self._start, primary=True)
        self._start_btn.pack(side="left", expand=True, fill="x", padx=(0, 6))
        self._stop_btn = self._btn(btn_row, "■  Stop", self._stop, danger=True)
        self._stop_btn.pack(side="left", expand=True, fill="x")
        self._stop_btn.config(state="disabled")

        # Print options
        print_frame = self._section("Print options")
        print_frame.pack(fill="x", padx=16, pady=(12, 16))

        self._chk_logo       = self._chk(print_frame, "Print logo",                True)
        self._chk_rotate     = self._chk(print_frame, "Rotate logo vertical",      False)
        self._chk_auto_print = self._chk(print_frame, "Auto-print after stitch",   True)

        tk.Frame(print_frame, bg=BORDER, height=1).pack(fill="x", padx=12, pady=6)

        opts = tk.Frame(print_frame, bg=SURFACE)
        opts.pack(fill="x", padx=12, pady=4)

        self._gamma_var = tk.StringVar(value="0.85 — default")
        self._clahe_var = tk.StringVar(value="Medium (2.0)")
        self._width_var = tk.StringVar(value="384 px — standard")

        for label, var, choices in [
            ("Gamma",            self._gamma_var, ["0.75 — brighter", "0.85 — default", "1.00 — linear"]),
            ("Contrast (CLAHE)", self._clahe_var, ["Low (1.0)", "Medium (2.0)", "High (3.5)"]),
            ("Printer width",    self._width_var, ["384 px — standard", "576 px — wide"]),
        ]:
            row = tk.Frame(opts, bg=SURFACE)
            row.pack(fill="x", pady=3)
            tk.Label(row, text=label, bg=SURFACE, fg=TEXT_LO, font=("Helvetica", 12), width=18, anchor="w").pack(side="left")
            om = tk.OptionMenu(row, var, *choices)
            om.config(bg=SURFACE, fg=TEXT_HI, activebackground=ACCENT,
                      activeforeground="#fff", highlightthickness=0,
                      font=("Helvetica", 12), relief="flat", bd=0)
            om["menu"].config(bg=SURFACE, fg=TEXT_HI, activebackground=ACCENT, activeforeground="#fff")
            om.pack(side="right")

        tk.Frame(print_frame, bg=BORDER, height=1).pack(fill="x", padx=12, pady=6)

        self._print_btn = self._btn(print_frame, "⎙  Print last panorama", self._print_now)
        self._print_btn.pack(fill="x", padx=12, pady=(0, 12))
        self._print_btn.config(state="disabled")

    # ── Helpers ───────────────────────────────────────────────────────────
    def _section(self, title):
        outer = tk.Frame(self, bg=BORDER, bd=0)
        inner = tk.Frame(outer, bg=SURFACE, bd=0)
        inner.pack(fill="both", expand=True, padx=1, pady=1)
        tk.Label(inner, text=title, bg=SURFACE, fg=TEXT_LO,
                 font=("Helvetica", 11), anchor="w").pack(fill="x", padx=12, pady=(8, 0))
        return inner

    def _btn(self, parent, text, cmd, primary=False, danger=False):
        if primary:
            b = tk.Button(parent, text=text, command=cmd,
                          bg=ACCENT, fg="#fff", activebackground="#5a4ed1",
                          activeforeground="#fff", relief="flat", bd=0,
                          font=("Helvetica", 13), padx=16, pady=10, cursor="hand2")
        elif danger:
            b = tk.Button(parent, text=text, command=cmd,
                          bg=SURFACE, fg=RED, activebackground="#2a1a1a",
                          activeforeground=RED, relief="flat", bd=1,
                          font=("Helvetica", 13), padx=16, pady=10, cursor="hand2",
                          highlightbackground=RED, highlightthickness=1)
        else:
            b = tk.Button(parent, text=text, command=cmd,
                          bg=SURFACE, fg=TEXT_HI, activebackground=BORDER,
                          activeforeground=TEXT_HI, relief="flat", bd=0,
                          font=("Helvetica", 12), padx=12, pady=8, cursor="hand2")
        return b

    def _chk(self, parent, text, default):
        var = tk.BooleanVar(value=default)
        tk.Checkbutton(
            parent, text=text, variable=var,
            bg=SURFACE, fg=TEXT_HI, selectcolor=ACCENT,
            activebackground=SURFACE, activeforeground=TEXT_HI,
            font=("Helvetica", 12)
        ).pack(anchor="w", padx=12, pady=2)
        return var

    def _set_status(self, mode, text):
        colours = {"idle": TEXT_LO, "running": ACCENT, "done": GREEN, "error": RED}
        c = colours.get(mode, TEXT_LO)
        self._dot.config(fg=c)
        self._status_lbl.config(text=text, fg=c)

    def _pip_colour(self, index, colour):
        c, r = self._pips[index]
        c.itemconfig(r, fill=colour)

    def _settings(self):
        gamma_map = {"0.75 — brighter": 0.75, "0.85 — default": 0.85, "1.00 — linear": 1.00}
        clahe_map = {"Low (1.0)": 1.0, "Medium (2.0)": 2.0, "High (3.5)": 3.5}
        width_map = {"384 px — standard": 384, "576 px — wide": 576}
        return {
            "print_logo":    self._chk_logo.get(),
            "rotate_logo":   self._chk_rotate.get(),
            "auto_print":    self._chk_auto_print.get(),
            "gamma":         gamma_map[self._gamma_var.get()],
            "clahe":         clahe_map[self._clahe_var.get()],
            "printer_width": width_map[self._width_var.get()],
        }

    # ── Feed ──────────────────────────────────────────────────────────────
    def _snap(self):
        self._refresh_btn.config(state="disabled", text="Capturing…")
        threading.Thread(target=self._snap_thread, daemon=True).start()

    def _snap_thread(self):
        try:
            path = capture_still_for_feed()
            self.after(0, self._show_image, path)
        except Exception as e:
            self.after(0, self._feed_lbl.config, {"text": f"Error: {e}"})
        finally:
            self.after(0, self._refresh_btn.config, {"state": "normal", "text": "⟳  Refresh"})
            self.after(0, self._ts_lbl.config, {"text": datetime.now().strftime("%H:%M:%S")})

    def _show_image(self, path):
        img = Image.open(path)
        w   = self._feed_lbl.winfo_width() or 640
        h   = self._feed_lbl.winfo_height() or 360
        img.thumbnail((w, h), Image.LANCZOS)
        self._feed_photo = ImageTk.PhotoImage(img)
        self._feed_lbl.config(image=self._feed_photo, text="")

    # ── Panorama ──────────────────────────────────────────────────────────
    def _start(self):
        self._start_btn.config(state="disabled")
        self._stop_btn.config(state="normal")
        self._print_btn.config(state="disabled")
        self._pbar["value"] = 0
        for i in range(len(ANGLES_TO_CAPTURE)):
            self._pip_colour(i, BORDER)
        self._pano_done = False
        self._set_status("running", "Starting…")
        s = self._settings()
        self._worker = threading.Thread(target=self._run_panorama, args=(s,), daemon=True)
        self._worker.start()

    def _stop(self):
        self._stopping = True
        self._set_status("idle", "Stopping…")

    def _run_panorama(self, s):
        global PRINT_LOGO_AT_END, ROTATE_LOGO_VERTICAL, PRINT_GAMMA, CLAHE_CLIP_LIMIT, PRINTER_WIDTH
        PRINT_LOGO_AT_END    = s["print_logo"]
        ROTATE_LOGO_VERTICAL = s["rotate_logo"]
        PRINT_GAMMA          = s["gamma"]
        CLAHE_CLIP_LIMIT     = s["clahe"]
        PRINTER_WIDTH        = s["printer_width"]
        self._stopping       = False

        def ui(fn): self.after(0, fn)
        def status(mode, text): ui(lambda: self._set_status(mode, text))

        try:
            status("running", "Setting up folders…")
            setup_image_folders()
            status("running", "Warming up camera…")
            warmup_camera()

            first = ANGLES_TO_CAPTURE[0]
            status("running", f"Moving to first angle ({first}°)…")
            move_servo(first)
            time.sleep(FIRST_CAPTURE_WAIT)
            flush_camera_once()

            total = len(ANGLES_TO_CAPTURE)
            for i, ang in enumerate(ANGLES_TO_CAPTURE):
                if self._stopping:
                    status("idle", "Stopped.")
                    ui(self._reset_buttons)
                    return
                if i != 0:
                    move_servo(ang)
                    time.sleep(FAST_SERVO_WAIT)
                status("running", f"Capturing {i+1}/{total} — {ang}°")
                ui(lambda i=i: self._pip_colour(i, "#afa9ec"))
                ui(lambda v=i+1: self._pbar.config(value=v))
                path = take_picture(ang, i)
                ui(lambda i=i: self._pip_colour(i, ACCENT))
                if path and os.path.exists(path):
                    ui(lambda p=path: self._show_image(p))

            subprocess.run(["sync"])
            move_servo(135)
            status("running", "Stitching…")
            success = stitch_images()

            if success and s["auto_print"]:
                status("running", "Printing…")
                process_and_print(STITCHED_OUTPUT)
                status("done", "Done — printed.")
            elif success:
                status("done", "Stitched. Ready to print.")
            else:
                status("error", "Stitching failed.")

            self._pano_done = success
            ui(lambda: self._print_btn.config(state="normal" if success else "disabled"))

        except Exception as e:
            self.after(0, lambda: self._set_status("error", f"Error: {e}"))

        finally:
            ui(self._reset_buttons)

    def _reset_buttons(self):
        self._start_btn.config(state="normal")
        self._stop_btn.config(state="disabled")

    # ── Print ─────────────────────────────────────────────────────────────
    def _print_now(self):
        s = self._settings()
        global PRINT_LOGO_AT_END, ROTATE_LOGO_VERTICAL, PRINT_GAMMA, CLAHE_CLIP_LIMIT, PRINTER_WIDTH
        PRINT_LOGO_AT_END    = s["print_logo"]
        ROTATE_LOGO_VERTICAL = s["rotate_logo"]
        PRINT_GAMMA          = s["gamma"]
        CLAHE_CLIP_LIMIT     = s["clahe"]
        PRINTER_WIDTH        = s["printer_width"]
        self._set_status("running", "Printing…")
        try:
            process_and_print(STITCHED_OUTPUT)
            self._set_status("done", "Print job sent.")
        except Exception as e:
            self._set_status("error", f"Print failed: {e}")

    def _on_close(self):
        shutdown_hardware()
        self.destroy()


# ============================================================
# ENTRY POINT
# ============================================================
if __name__ == "__main__":
    if not init_hardware():
        print("Running without hardware.")
    app = App()
    app.mainloop()
