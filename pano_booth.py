"""
Panorama Photo Booth — Single-file script with integrated tkinter UI
Screen: 800×480 (Raspberry Pi 7" official display)

Requirements:
    pip install pigpio picamera2 opencv-python imutils gpiozero python-escpos pillow

Run:
    sudo pigpiod
    python pano_booth.py
"""

import os, shutil, glob, subprocess, threading, time
from datetime import datetime
import tkinter as tk
from tkinter import font as tkfont
from PIL import Image, ImageEnhance, ExifTags, ImageTk
import pigpio
import cv2, numpy as np, imutils
from picamera2 import Picamera2
from gpiozero import Button
from escpos.printer import Usb

# ============================================================
# PIN CONFIGURATION
# ============================================================
SERVO_PIN  = 26
BUTTON_PIN = 17

# ============================================================
# FOLDER CONFIGURATION
# ============================================================
SAVE_DIR            = os.path.expanduser("~/photos")
IMAGE_FOLDER        = "imageprinter"
UNSTITCHED_FOLDER   = os.path.join(IMAGE_FOLDER, "unstitchedImages")
STITCHED_OUTPUT     = os.path.join(IMAGE_FOLDER, "stitchedOutputProcessed.png")
RAW_STITCHED_OUTPUT = os.path.join(IMAGE_FOLDER, "stitchedOutputRaw.png")
STITCH_RESIZE_WIDTH = 1200

# ============================================================
# SPEED + SHARPNESS SETTINGS
# ============================================================
FAST_SERVO_WAIT  = 0.28
EXPOSURE_TIME_US = 8000
ANALOGUE_GAIN    = 8.0
IMAGE_WIDTH      = 1920
IMAGE_HEIGHT     = 1920
ANGLES_TO_CAPTURE = [270, 245, 220, 195, 170, 145, 120, 95, 70, 45, 20, 0]
MIN_PULSE   = 500
MAX_PULSE   = 2500
MAX_DEGREES = 270

# ============================================================
# SETUP
# ============================================================
os.makedirs(SAVE_DIR, exist_ok=True)

pi = pigpio.pi()
if not pi.connected:
    raise RuntimeError("Could not connect to pigpiod. Run: sudo pigpiod")

button = Button(BUTTON_PIN, pull_up=True, bounce_time=0.05)

camera = Picamera2()
camera.configure(camera.create_still_configuration(
    main={"size": (IMAGE_WIDTH, IMAGE_HEIGHT)}
))
camera.start()
time.sleep(2)

# ============================================================
# SERVO HELPERS  (unchanged)
# ============================================================
def angle_to_pulse(angle):
    angle = max(0, min(MAX_DEGREES, angle))
    return int(MIN_PULSE + (angle / MAX_DEGREES) * (MAX_PULSE - MIN_PULSE))

def move_servo(angle):
    pulse = angle_to_pulse(angle)
    pi.set_servo_pulsewidth(SERVO_PIN, pulse)

# ============================================================
# IMAGE CAPTURE  (unchanged)
# ============================================================
def setup_image_folders():
    if os.path.exists(IMAGE_FOLDER):
        shutil.rmtree(IMAGE_FOLDER)
    os.makedirs(UNSTITCHED_FOLDER)

def take_picture(angle, index):
    filename = os.path.join(UNSTITCHED_FOLDER, f"{index:02d}_angle_{angle:03d}.jpg")
    camera.capture_file(filename)
    time.sleep(0.05)
    return filename

# ============================================================
# PANORAMA STITCHING  (unchanged)
# ============================================================
def extract_capture_index(path):
    return int(os.path.basename(path).split("_")[0])

def crop_only_outer_black(stitched_img):
    gray = cv2.cvtColor(stitched_img, cv2.COLOR_BGR2GRAY)
    ys, xs = np.where(gray > 0)
    if len(xs) == 0 or len(ys) == 0:
        return stitched_img
    return stitched_img[np.min(ys):np.max(ys)+1, np.min(xs):np.max(xs)+1]

def stitch_images():
    image_paths = sorted(
        glob.glob(os.path.join(UNSTITCHED_FOLDER, "*.jpg")),
        key=extract_capture_index
    )
    images = []
    for p in image_paths:
        img = cv2.imread(p)
        if img is not None:
            images.append(imutils.resize(img, width=STITCH_RESIZE_WIDTH))

    if len(images) < 2:
        raise ValueError("Need at least 2 images to stitch.")

    stitcher = cv2.Stitcher_create(cv2.Stitcher_PANORAMA)
    status, stitched = stitcher.stitch(images)

    if status == cv2.Stitcher_OK:
        cv2.imwrite(RAW_STITCHED_OUTPUT, stitched)
        stitched = cv2.copyMakeBorder(stitched, 10, 10, 10, 10,
                                      cv2.BORDER_CONSTANT, (0, 0, 0))
        cleaned = crop_only_outer_black(stitched)
        cv2.imwrite(STITCHED_OUTPUT, cleaned)
        return True

    print("Stitching failed. Status:", status)
    return False

# ============================================================
# THERMAL PRINTING  (unchanged)
# ============================================================
def process_and_print(filepath):
    img = Image.open(filepath)
    try:
        exif = img._getexif()
        if exif:
            for tag, value in exif.items():
                if ExifTags.TAGS.get(tag) == 'Orientation':
                    if value == 3:   img = img.rotate(180, expand=True)
                    elif value == 6: img = img.rotate(270, expand=True)
                    elif value == 8: img = img.rotate(90,  expand=True)
                    break
    except Exception:
        pass

    img = img.convert("L")
    img = ImageEnhance.Brightness(img).enhance(1.0)
    img = ImageEnhance.Contrast(img).enhance(1.5)
    img = ImageEnhance.Sharpness(img).enhance(1.5)
    img = img.rotate(90, expand=True)

    width = 384
    img = img.resize((width, int(img.height * width / img.width)), Image.LANCZOS)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    temp_path = os.path.join(SAVE_DIR, f"panorama_{timestamp}.png")
    img.save(temp_path)

    p = Usb(0x0485, 0x5741)
    p.hw('INIT')
    p.image(temp_path)
    p.text("\n")
    p.cut()

# ============================================================
# TKINTER UI
# ============================================================

# ── Palette ──────────────────────────────────────────────────
BG        = "#09090b"
SURFACE   = "#111115"
SURFACE2  = "#18181e"
BORDER    = "#232330"
TEXT      = "#e4e4f0"
MUTED     = "#5a5a78"
FAINT     = "#2a2a3a"
ACCENT    = "#7c6af7"
ACCENT2   = "#a597ff"
GREEN     = "#3ecf74"
AMBER     = "#f0a830"
RED       = "#f05050"

W, H      = 800, 480
FEED_W    = 560   # left feed area width
SIDE_W    = W - FEED_W  # 240

TOTAL_ANGLES = len(ANGLES_TO_CAPTURE)


class BoothUI(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Panorama Booth")
        self.geometry(f"{W}x{H}")
        self.resizable(False, False)
        self.configure(bg=BG)
        self.attributes("-fullscreen", True)

        # Fonts
        self.f_mono_lg  = tkfont.Font(family="Courier", size=14, weight="bold")
        self.f_mono_md  = tkfont.Font(family="Courier", size=11)
        self.f_mono_sm  = tkfont.Font(family="Courier", size=9)
        self.f_sans_lg  = tkfont.Font(family="DejaVu Sans", size=13, weight="bold")
        self.f_sans_md  = tkfont.Font(family="DejaVu Sans", size=11)
        self.f_sans_sm  = tkfont.Font(family="DejaVu Sans", size=9)

        self._photo_ref = None   # keep ImageTk ref alive
        self._is_running = False

        self._build()
        self._update_clock()
        self._poll_ui()

        # Hook physical button
        button.when_pressed = self._on_button_press

        # Escape to quit
        self.bind("<Escape>", lambda e: self._quit())

    # ── Layout ───────────────────────────────────────────────
    def _build(self):
        # ── Top bar ──
        bar = tk.Frame(self, bg=SURFACE, height=36)
        bar.pack(fill="x", side="top")
        bar.pack_propagate(False)

        tk.Label(bar, text="PANO / BOOTH", font=self.f_mono_md,
                 bg=SURFACE, fg=ACCENT2).pack(side="left", padx=14)

        self.lbl_clock = tk.Label(bar, text="00:00:00", font=self.f_mono_sm,
                                  bg=SURFACE, fg=MUTED)
        self.lbl_clock.pack(side="right", padx=14)

        self.lbl_phase_pill = tk.Label(bar, text="  READY  ", font=self.f_mono_sm,
                                       bg="#0d2b1a", fg=GREEN,
                                       relief="flat", padx=6, pady=2)
        self.lbl_phase_pill.pack(side="right", padx=6)

        # ── Body ──
        body = tk.Frame(self, bg=BG)
        body.pack(fill="both", expand=True)

        # Feed area
        feed = tk.Frame(body, bg="#050507", width=FEED_W)
        feed.pack(side="left", fill="both", expand=True)
        feed.pack_propagate(False)

        self.feed_canvas = tk.Canvas(feed, bg="#050507",
                                     highlightthickness=0)
        self.feed_canvas.pack(fill="both", expand=True)
        self.feed_canvas.bind("<Configure>", self._on_feed_resize)

        # Feed bottom bar
        fbar = tk.Frame(feed, bg=SURFACE, height=24)
        fbar.pack(fill="x", side="bottom")
        fbar.pack_propagate(False)
        self.lbl_feed_info = tk.Label(fbar, text="Awaiting capture",
                                      font=self.f_mono_sm, bg=SURFACE, fg=MUTED)
        self.lbl_feed_info.pack(side="left", padx=10)

        # Sidebar
        side = tk.Frame(body, bg=SURFACE, width=SIDE_W)
        side.pack(side="right", fill="y")
        side.pack_propagate(False)

        self._build_sidebar(side)

        # ── Footer ──
        foot = tk.Frame(self, bg=SURFACE, height=26)
        foot.pack(fill="x", side="bottom")
        foot.pack_propagate(False)
        tk.Label(foot, text="GPIO 26 · Servo    GPIO 17 · Button    Camera Module v3",
                 font=self.f_mono_sm, bg=SURFACE, fg=FAINT).pack(side="left", padx=12)
        self.lbl_last = tk.Label(foot, text="—", font=self.f_mono_sm,
                                 bg=SURFACE, fg=MUTED)
        self.lbl_last.pack(side="right", padx=12)

    def _build_sidebar(self, parent):
        pad = {"padx": 12, "pady": 4}

        # Phase label
        tk.Label(parent, text="STATUS", font=self.f_mono_sm,
                 bg=SURFACE, fg=FAINT).pack(anchor="w", padx=12, pady=(10,2))

        self.lbl_phase = tk.Label(parent, text="Idle", font=self.f_sans_lg,
                                  bg=SURFACE, fg=TEXT)
        self.lbl_phase.pack(anchor="w", padx=12)

        self.lbl_msg = tk.Label(parent, text="Ready. Press the button.",
                                font=self.f_sans_sm, bg=SURFACE, fg=MUTED,
                                wraplength=SIDE_W-24, justify="left")
        self.lbl_msg.pack(anchor="w", padx=12, pady=(2,6))

        # Progress bar
        prog_frame = tk.Frame(parent, bg=SURFACE)
        prog_frame.pack(fill="x", padx=12, pady=(0,2))
        self.prog_track = tk.Frame(prog_frame, bg=BORDER, height=4)
        self.prog_track.pack(fill="x")
        self.prog_track.pack_propagate(False)
        self.prog_bar = tk.Frame(self.prog_track, bg=ACCENT, height=4, width=0)
        self.prog_bar.place(x=0, y=0, height=4)

        self.lbl_prog = tk.Label(parent, text="", font=self.f_mono_sm,
                                 bg=SURFACE, fg=MUTED)
        self.lbl_prog.pack(anchor="w", padx=12)

        # Angle dot grid
        tk.Label(parent, text="CAPTURES", font=self.f_mono_sm,
                 bg=SURFACE, fg=FAINT).pack(anchor="w", padx=12, pady=(8,2))

        dot_frame = tk.Frame(parent, bg=SURFACE)
        dot_frame.pack(anchor="w", padx=12)
        self._dots = []
        for i in range(TOTAL_ANGLES):
            d = tk.Frame(dot_frame, bg=FAINT, width=14, height=14)
            d.grid(row=i//6, column=i%6, padx=2, pady=2)
            d.grid_propagate(False)
            self._dots.append(d)

        # Separator
        tk.Frame(parent, bg=BORDER, height=1).pack(fill="x", padx=0, pady=8)

        # Trigger button
        self.btn_trigger = tk.Button(
            parent, text="▶  START PANORAMA",
            font=self.f_mono_md, bg=ACCENT, fg="white",
            activebackground=ACCENT2, activeforeground="white",
            relief="flat", cursor="hand2", pady=10,
            command=self._on_trigger
        )
        self.btn_trigger.pack(fill="x", padx=12, pady=(0,6))

        self.btn_reset = tk.Button(
            parent, text="↺  RESET",
            font=self.f_mono_sm, bg=SURFACE2, fg=MUTED,
            activebackground=BORDER, activeforeground=TEXT,
            relief="flat", cursor="hand2", pady=6,
            state="disabled", command=self._on_reset
        )
        self.btn_reset.pack(fill="x", padx=12)

        # Separator
        tk.Frame(parent, bg=BORDER, height=1).pack(fill="x", padx=0, pady=8)

        # Info stats
        stats = [
            ("Shots",      f"{TOTAL_ANGLES} angles"),
            ("Resolution", "1920×1920"),
            ("Exposure",   "8000 µs"),
            ("Gain",       "8.0"),
        ]
        for key, val in stats:
            row = tk.Frame(parent, bg=SURFACE)
            row.pack(fill="x", padx=12, pady=1)
            tk.Label(row, text=key, font=self.f_sans_sm,
                     bg=SURFACE, fg=MUTED).pack(side="left")
            tk.Label(row, text=val, font=self.f_mono_sm,
                     bg=SURFACE, fg=TEXT).pack(side="right")

    # ── Feed canvas image ─────────────────────────────────────
    def _on_feed_resize(self, event):
        self._feed_w = event.width
        self._feed_h = event.height

    def _show_image(self, pil_img):
        fw = getattr(self, '_feed_w', FEED_W)
        fh = getattr(self, '_feed_h', H - 36 - 24 - 26)

        pil_img.thumbnail((fw, fh), Image.LANCZOS)
        photo = ImageTk.PhotoImage(pil_img)
        self._photo_ref = photo

        c = self.feed_canvas
        c.delete("all")
        cx, cy = fw // 2, fh // 2
        c.create_image(cx, cy, image=photo, anchor="center")

        # Corner brackets
        m = 10
        for x1, y1, x2, y2 in [
            (m, m, m+14, m), (m, m, m, m+14),
            (fw-m, m, fw-m-14, m), (fw-m, m, fw-m, m+14),
            (m, fh-m, m+14, fh-m), (m, fh-m, m, fh-m-14),
            (fw-m, fh-m, fw-m-14, fh-m), (fw-m, fh-m, fw-m, fh-m-14),
        ]:
            c.create_line(x1, y1, x2, y2, fill=ACCENT, width=1)

    def _show_placeholder(self, text="Awaiting first capture"):
        c = self.feed_canvas
        c.delete("all")
        fw = getattr(self, '_feed_w', FEED_W)
        fh = getattr(self, '_feed_h', H - 36 - 24 - 26)
        c.create_text(fw//2, fh//2, text=text, fill=FAINT,
                      font=self.f_mono_sm, anchor="center")

    # ── Phase / progress state ────────────────────────────────
    # These are called from the background thread via self.after()

    def set_phase(self, phase, msg, step=0, angle=None):
        """phase: idle | capturing | stitching | printing | done | error"""
        phase_labels = {
            "idle":      ("Idle",       TEXT,   "  READY  ",   "#0d2b1a", GREEN),
            "capturing": ("Capturing",  ACCENT2,"  RUNNING ",  "#1a1530", ACCENT2),
            "stitching": ("Stitching",  AMBER,  "  STITCHING ","#1a1200", AMBER),
            "printing":  ("Printing",   ACCENT, "  PRINTING ", "#130e2b", ACCENT),
            "done":      ("Done",       GREEN,  "  DONE  ",    "#0d2b1a", GREEN),
            "error":     ("Error",      RED,    "  ERROR  ",   "#2b0d0d", RED),
        }
        label, color, pill_text, pill_bg, pill_fg = phase_labels.get(
            phase, ("Idle", TEXT, "  READY  ", "#0d2b1a", GREEN))

        self.lbl_phase.config(text=label, fg=color)
        self.lbl_msg.config(text=msg)
        self.lbl_phase_pill.config(text=pill_text, bg=pill_bg, fg=pill_fg)

        running = phase in ("capturing", "stitching", "printing")
        self.btn_trigger.config(state="disabled" if running else "normal")
        self.btn_reset.config(state="disabled" if running else "normal")

        # Progress bar
        if phase == "capturing":
            pct = step / TOTAL_ANGLES
            track_w = self.prog_track.winfo_width() or (SIDE_W - 24)
            self.prog_bar.place(x=0, y=0, height=4, width=int(track_w * pct))
            angle_str = f"{angle}°" if angle is not None else "—"
            self.lbl_prog.config(text=f"{step} / {TOTAL_ANGLES}  ·  {angle_str}")
        else:
            self.prog_bar.place(x=0, y=0, height=4, width=0)
            self.lbl_prog.config(text="")

        # Dots
        for i, d in enumerate(self._dots):
            if phase == "capturing":
                if i < step:      d.config(bg=ACCENT)
                elif i == step:   d.config(bg=ACCENT2)
                else:             d.config(bg=FAINT)
            elif phase in ("stitching", "printing"):
                d.config(bg=ACCENT)
            elif phase == "done":
                d.config(bg=GREEN)
            else:
                d.config(bg=FAINT)

    def set_feed_label(self, text):
        self.lbl_feed_info.config(text=text)

    def set_last_event(self, text):
        self.lbl_last.config(text=text)

    # ── Clock ─────────────────────────────────────────────────
    def _update_clock(self):
        now = datetime.now().strftime("%H:%M:%S")
        self.lbl_clock.config(text=now)
        self.after(1000, self._update_clock)

    # ── Poll: blink trigger button while running ──────────────
    _blink_state = False
    def _poll_ui(self):
        if self._is_running:
            self._blink_state = not self._blink_state
            col = ACCENT2 if self._blink_state else ACCENT
            self.btn_trigger.config(bg=col)
        else:
            self.btn_trigger.config(bg=ACCENT)
        self.after(500, self._poll_ui)

    # ── Actions ───────────────────────────────────────────────
    def _on_trigger(self):
        if self._is_running:
            return
        threading.Thread(target=self._run_booth, daemon=True).start()

    def _on_button_press(self):
        # gpiozero calls this from its own thread — safe to dispatch
        self.after(0, self._on_trigger)

    def _on_reset(self):
        self._show_placeholder()
        self.set_phase("idle", "Ready. Press the button.")
        self.set_feed_label("Awaiting capture")
        self.set_last_event("—")

    def _quit(self):
        camera.stop()
        pi.set_servo_pulsewidth(SERVO_PIN, 0)
        pi.stop()
        self.destroy()

    # ── Main booth flow (background thread) ───────────────────
    def _run_booth(self):
        if self._is_running:
            return
        self._is_running = True

        def ui(fn, *a, **kw):
            self.after(0, lambda: fn(*a, **kw))

        try:
            ui(self.set_phase, "capturing", "Setting up folders…", 0)
            setup_image_folders()

            camera.set_controls({
                "AeEnable":     False,
                "ExposureTime": EXPOSURE_TIME_US,
                "AnalogueGain": ANALOGUE_GAIN,
                "AwbEnable":    True,
            })
            time.sleep(0.3)

            for index, angle in enumerate(ANGLES_TO_CAPTURE):
                msg = f"Capturing angle {angle}° ({index+1}/{TOTAL_ANGLES})"
                ui(self.set_phase, "capturing", msg, index, angle)
                move_servo(angle)
                time.sleep(FAST_SERVO_WAIT)
                filepath = take_picture(angle, index)

                # Show the latest shot on the feed
                try:
                    img = Image.open(filepath)
                    ui(self._show_image, img.copy())
                    ui(self.set_feed_label, f"Shot {index+1} · {angle}°")
                except Exception:
                    pass

            ui(self.set_phase, "stitching", "Flushing and stitching panorama…")
            subprocess.run(["sync"])
            move_servo(135)

            success = stitch_images()

            if success and os.path.exists(STITCHED_OUTPUT):
                # Show stitched result
                try:
                    img = Image.open(STITCHED_OUTPUT)
                    ui(self._show_image, img.copy())
                    ui(self.set_feed_label, "Stitched panorama")
                except Exception:
                    pass

                ui(self.set_phase, "printing", "Sending to thermal printer…")
                try:
                    process_and_print(STITCHED_OUTPUT)
                    ui(self.set_phase, "done", "Panorama printed successfully!")
                    ui(self.set_last_event, f"Last print: {datetime.now().strftime('%H:%M:%S')}")
                except Exception as e:
                    ui(self.set_phase, "error", f"Print failed: {e}")
            else:
                ui(self.set_phase, "error",
                   "Stitching failed. Check image overlap and lighting.")

        except Exception as e:
            ui(self.set_phase, "error", f"Error: {e}")

        finally:
            self._is_running = False
            print("--- Ready for next shot ---")


# ============================================================
# ENTRY POINT
# ============================================================
if __name__ == "__main__":
    app = BoothUI()
    app.mainloop()

    # Cleanup after window closes
    camera.stop()
    pi.set_servo_pulsewidth(SERVO_PIN, 0)
    pi.stop()

