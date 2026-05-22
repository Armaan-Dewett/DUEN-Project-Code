#!/usr/bin/env python3
"""
DUEN Panoramic Photobooth - single-file rewrite.

Hardware:
    Raspberry Pi 4
    Pi Camera v3 (or v2) via picamera2 / libcamera
    MG996R / MG90S servo on GPIO 26 driven by pigpio
    WS2812 LED ring (61 px) on GPIO 18 via rpi_ws281x
    USB ESCPOS thermal printer (0x0485 / 0x5741)
    Four NO momentary push buttons:
        Capture       -> GPIO 17 (pin 11)  -> GND pin 9
        Flash cycle   -> GPIO 27 (pin 13)  -> GND pin 14
        Preview       -> GPIO 22 (pin 15)  -> GND pin 14
        Emergency stop-> GPIO 23 (pin 16)  -> GND pin 20

Runtime:
    sudo pigpiod
    python3 duen_booth.py

Set DUEN_HEADLESS=1 to develop off-Pi with stub hardware.
"""

import os
import sys
import glob
import shutil
import time
import threading
import subprocess
import selectors
from datetime import datetime

import tkinter as tk
from PIL import Image, ImageDraw, ImageEnhance, ImageTk, ExifTags


HEADLESS = os.environ.get("DUEN_HEADLESS", "0") == "1"


# ----------------------------------------------------------------------------
# Hardware imports with HEADLESS fallback stubs.
# Real Pi imports succeed; off-Pi dev with DUEN_HEADLESS=1 uses the stubs.
# ----------------------------------------------------------------------------

class _FakePi:
    connected = True
    def set_servo_pulsewidth(self, *_a, **_kw): pass
    def stop(self): pass


try:
    import pigpio
except ImportError as e:
    if not HEADLESS:
        raise RuntimeError("pigpio is required. Install pigpio and run: sudo pigpiod") from e
    class _FakePigpio:
        @staticmethod
        def pi(): return _FakePi()
    pigpio = _FakePigpio()


try:
    import cv2
    import numpy as np
    import imutils
except ImportError as e:
    if not HEADLESS:
        raise RuntimeError("cv2, numpy, and imutils are required.") from e
    cv2 = None
    np = None
    imutils = None


try:
    from picamera2 import Picamera2
except ImportError as e:
    if not HEADLESS:
        raise RuntimeError("picamera2 is required on the Raspberry Pi.") from e
    Picamera2 = None


try:
    from gpiozero import Button
except ImportError as e:
    if not HEADLESS:
        raise RuntimeError("gpiozero is required for the physical buttons.") from e
    class Button:
        def __init__(self, pin, **_kw):
            self.pin = pin
            self.when_pressed = None


try:
    from escpos.printer import Usb
except ImportError as e:
    if not HEADLESS:
        raise RuntimeError("python-escpos is required for USB thermal printing.") from e
    class Usb:
        def __init__(self, *_a, **_kw): pass
        def hw(self, *_a, **_kw): pass
        def image(self, *_a, **_kw): pass
        def text(self, *_a, **_kw): pass
        def cut(self, *_a, **_kw): pass


try:
    from rpi_ws281x import PixelStrip, Color
except ImportError as e:
    if not HEADLESS:
        raise RuntimeError("rpi_ws281x is required for the LED ring.") from e
    def Color(r, g, b): return (int(r), int(g), int(b))
    class PixelStrip:
        def __init__(self, count, *_a, **_kw):
            self._n = count
        def begin(self): pass
        def numPixels(self): return self._n
        def setBrightness(self, _v): pass
        def setPixelColor(self, _i, _c): pass
        def show(self): pass


# ----------------------------------------------------------------------------
# Configuration
# ----------------------------------------------------------------------------

# GPIO pin assignments. Servo stays on 26; printer is USB so no conflict.
SERVO_PIN       = 26   # header pin 37
LED_PIN         = 18   # header pin 12
CAPTURE_BTN_PIN = 17   # header pin 11  -> GND pin 9
FLASH_BTN_PIN   = 27   # header pin 13  -> GND pin 14
PREVIEW_BTN_PIN = 22   # header pin 15  -> GND pin 14
ESTOP_BTN_PIN   = 23   # header pin 16  -> GND pin 20

# Folders and paths
BASE_DIR            = os.path.dirname(os.path.abspath(__file__))
SAVE_DIR            = os.path.expanduser("~/photos")
IMAGE_FOLDER        = os.path.join(BASE_DIR, "imageprinter")
UNSTITCHED_FOLDER   = os.path.join(IMAGE_FOLDER, "unstitchedImages")
STITCHED_OUTPUT     = os.path.join(IMAGE_FOLDER, "stitchedOutputProcessed.png")
RAW_STITCHED_OUTPUT = os.path.join(IMAGE_FOLDER, "stitchedOutputRaw.png")
DISCARD_PATH        = os.path.join(IMAGE_FOLDER, "discard.jpg")
LOGO_PATH           = os.path.join(BASE_DIR, "duen_logo.png")
PRINT_COUNT_FILE    = os.path.join(SAVE_DIR, ".print_count")

# Servo sweep: 16 angles proven to work in perfect_stitch.py.
# Avoids the mechanical hard-stops at 0° and 270° that cause the servo to
# stutter and blur the first/last frames.
ANGLES_TO_CAPTURE = [265, 247, 229, 211, 193, 175, 157, 139,
                     121, 103,  88,  73,  58,  43,  28,  13]
MIN_PULSE   = 500
MAX_PULSE   = 2500
MAX_DEGREES = 270

# Capture timing.
FIRST_SHOT_SETTLE_S = 1.2     # blurry-first-photo fix
FAST_SERVO_WAIT     = 0.28
POST_CAPTURE_WAIT   = 0.15
IMAGE_WIDTH         = 1920
IMAGE_HEIGHT        = 1920
EXPOSURE_TIME_US    = 8000
ANALOGUE_GAIN       = 4.0

# Live preview.
PREVIEW_W      = 640
PREVIEW_H      = 480
PREVIEW_FPS_HZ = 12

# Thermal printer image processing.
PRINTER_VID          = 0x0485
PRINTER_PID          = 0x5741
PRINTER_WIDTH        = 384
STITCH_RESIZE_WIDTH  = 800   # 600 caused hangs/dropped frames; 800 is the safe minimum
WHITE_MAX_LEVEL      = 0.88
HIGHLIGHT_STRENGTH   = 0.18
PRINT_GAMMA          = 0.85
CLAHE_CLIP_LIMIT     = 2.0
BRIGHT_NOISE_AMOUNT  = 10
PRINT_LOGO_AT_END    = True
ROTATE_LOGO_VERTICAL = False
LOGO_ROTATION_DEGREES       = 270
LOGO_VERTICAL_WIDTH_SCALE   = 0.55
LOGO_HORIZONTAL_WIDTH_SCALE = 0.50
LOGO_PADDING_PX             = 40

# LED ring.
LED_COUNT              = 61
LED_FREQ_HZ            = 800000
LED_DMA                = 10
LED_INVERT             = False
LED_CHANNEL            = 0
LED_DEFAULT_BRIGHTNESS = 128
SNAKE_TAIL_LEN         = 6
SNAKE_STEP_MS          = 40
DISCO_STEP_MS          = 20

LIGHT_COLORS = {
    "white": (255, 255, 255),
    "warm":  (255, 170,  60),
    "red":   (255,   0,   0),
}
FLASH_CYCLE     = ("off", "white", "warm", "red")
COUNTDOWN_CYCLE = (0, 3, 5, 10)
MAX_PRINTS_PER_RUN = 6

# UI window, palette, fonts.
WIN_W, WIN_H = 800, 480

BG       = "#09090b"
SURFACE  = "#111115"
SURFACE2 = "#18181e"
BORDER   = "#232330"
TEXT     = "#e4e4f0"
MUTED    = "#5a5a78"
FAINT    = "#2a2a3a"
ACCENT   = "#7c6af7"
ACCENT2  = "#a597ff"
GREEN    = "#3ecf74"
AMBER    = "#f0a830"
RED      = "#f05050"

F_MONO_LG = ("Courier", 14, "bold")
F_MONO_MD = ("Courier", 11, "normal")
F_MONO_SM = ("Courier", 9,  "normal")
F_SANS_LG = ("DejaVu Sans", 13, "bold")
F_SANS_MD = ("DejaVu Sans", 11, "normal")
F_SANS_SM = ("DejaVu Sans", 9,  "normal")
F_PHASE   = ("DejaVu Sans", 16, "bold")
F_COUNT   = ("Courier", 96, "bold")
F_QTY     = ("Courier", 28, "bold")
F_QTY_BTN = ("Courier", 20, "bold")


# ----------------------------------------------------------------------------
# Module-level hardware bring-up.
# Done at import so worker threads can call move_servo / capture / print
# without re-initializing.
# ----------------------------------------------------------------------------

os.makedirs(SAVE_DIR, exist_ok=True)


pi = pigpio.pi()
if not pi.connected:
    if not HEADLESS:
        raise RuntimeError("Could not connect to pigpiod. Run: sudo pigpiod")
    pi = _FakePi()


def _make_synthetic_image(path, label="", size=(IMAGE_WIDTH, IMAGE_HEIGHT)):
    """Write a labelled synthetic JPEG. Used in HEADLESS dev mode."""
    import colorsys
    h = (sum(ord(c) for c in label) * 37) % 360
    r, g, b = colorsys.hsv_to_rgb(h / 360.0, 0.45, 0.6)
    bg = (int(r * 255), int(g * 255), int(b * 255))
    img = Image.new("RGB", size, bg)
    d = ImageDraw.Draw(img)
    d.rectangle([0, 0, size[0] - 1, size[1] - 1], outline=(255, 255, 255), width=6)
    d.text((30, 30), label or "synthetic", fill=(255, 255, 255))
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    img.save(path, "JPEG", quality=85)


if HEADLESS:
    class _FakeCamera:
        def configure(self, *_a, **_kw): pass
        def create_still_configuration(self, **_kw): return {}
        def start(self): pass
        def stop(self): pass
        def set_controls(self, *_a, **_kw): pass
        def capture_metadata(self):
            return {"ExposureTime": EXPOSURE_TIME_US, "AnalogueGain": ANALOGUE_GAIN}
        def capture_file(self, path):
            _make_synthetic_image(path, label=os.path.basename(path))
        def capture_array(self, _which="lores"):
            return None
    camera = _FakeCamera()
else:
    camera = Picamera2()
    camera.configure(camera.create_still_configuration(
        main={"size": (IMAGE_WIDTH, IMAGE_HEIGHT)},
        lores={"size": (PREVIEW_W, PREVIEW_H), "format": "YUV420"},
        display="lores",
    ))
    camera.start()
    time.sleep(2.0)


strip = PixelStrip(LED_COUNT, LED_PIN, LED_FREQ_HZ,
                   LED_DMA, LED_INVERT, LED_DEFAULT_BRIGHTNESS, LED_CHANNEL)
strip.begin()


# ----------------------------------------------------------------------------
# Servo
# ----------------------------------------------------------------------------

def angle_to_pulse(angle):
    angle = max(0, min(MAX_DEGREES, angle))
    return int(MIN_PULSE + (angle / MAX_DEGREES) * (MAX_PULSE - MIN_PULSE))


def move_servo(angle):
    pi.set_servo_pulsewidth(SERVO_PIN, angle_to_pulse(angle))


# ----------------------------------------------------------------------------
# Capture
# ----------------------------------------------------------------------------

def setup_image_folders():
    if os.path.exists(IMAGE_FOLDER):
        shutil.rmtree(IMAGE_FOLDER)
    os.makedirs(UNSTITCHED_FOLDER)


def safe_capture_metadata():
    if HEADLESS:
        return {"ExposureTime": EXPOSURE_TIME_US, "AnalogueGain": ANALOGUE_GAIN}
    try:
        return camera.capture_metadata()
    except Exception:
        time.sleep(0.3)
        return camera.capture_metadata()


def warmup_camera():
    """Lock exposure and white balance at the center of the sweep."""
    move_servo(135)
    time.sleep(0.7)
    camera.set_controls({"AeEnable": True, "AwbEnable": True})
    for _ in range(5):
        time.sleep(0.25)
        safe_capture_metadata()
    md = safe_capture_metadata()
    locked_exposure = md.get("ExposureTime", EXPOSURE_TIME_US)
    locked_gain     = md.get("AnalogueGain", ANALOGUE_GAIN)
    camera.set_controls({
        "AeEnable": False,
        "ExposureTime": locked_exposure,
        "AnalogueGain": locked_gain,
        "AwbEnable": True,
    })
    time.sleep(0.4)


def take_picture(angle, index):
    filename = os.path.join(
        UNSTITCHED_FOLDER, f"{index:02d}_angle_{angle:03d}.jpg"
    )
    if HEADLESS:
        _make_synthetic_image(filename, label=f"{index:02d}  {angle} deg")
        time.sleep(POST_CAPTURE_WAIT)
        return filename
    try:
        camera.capture_file(filename)
    except Exception as e:
        print(f"capture failed at angle {angle}: {e}; retrying")
        time.sleep(0.5)
        camera.capture_file(filename)
    time.sleep(POST_CAPTURE_WAIT)
    return filename


def discard_capture():
    """Throw-away capture that flushes the AGC pipeline.

    Run after the servo settles at the first angle but before the first real
    shot. Without this, the first frame is reliably blurry because the camera
    is mid-exposure-adjust when the first capture lands.
    """
    if HEADLESS:
        _make_synthetic_image(DISCARD_PATH, label="discard")
        return
    try:
        os.makedirs(os.path.dirname(DISCARD_PATH), exist_ok=True)
        camera.capture_file(DISCARD_PATH)
    except Exception:
        pass


# ----------------------------------------------------------------------------
# Stitching
# Taken from perfect_stitch.py / duen_hardware.py - unchanged algorithm.
# Added an optional progress_cb so the UI can show per-stage status.
# ----------------------------------------------------------------------------

def extract_capture_index(path):
    return int(os.path.basename(path).split("_")[0])


def crop_only_outer_black(stitched_img):
    """Largest interior rectangle with zero black pixels.

    Histogram + stack algorithm per row. Identical to the one in
    perfect_stitch.py - this is the load-bearing crop that makes the panorama
    look clean instead of having ragged black wedges at the edges.
    """
    gray = cv2.cvtColor(stitched_img, cv2.COLOR_BGR2GRAY)
    mask = (gray > 10).astype(np.uint8)
    if mask.sum() == 0:
        return stitched_img

    rows, cols = mask.shape
    heights = np.zeros(cols, dtype=np.int32)
    best_area = 0
    best_rect = (0, 0, rows, cols)

    for row in range(rows):
        heights = np.where(mask[row] == 1, heights + 1, 0)
        stack = []
        i = 0
        while i <= cols:
            h = heights[i] if i < cols else 0
            if not stack or h >= heights[stack[-1]]:
                stack.append(i)
                i += 1
            else:
                top = stack.pop()
                width = i if not stack else i - stack[-1] - 1
                height = heights[top]
                area = width * height
                if area > best_area:
                    best_area = area
                    x1 = (i - width) if not stack else stack[-1] + 1
                    x2 = x1 + width
                    y2 = row + 1
                    y1 = y2 - height
                    best_rect = (y1, x1, y2, x2)

    y1, x1, y2, x2 = best_rect
    return stitched_img[y1:y2, x1:x2]


def load_images_for_stitching():
    image_paths = sorted(
        glob.glob(os.path.join(UNSTITCHED_FOLDER, "*.jpg")),
        key=extract_capture_index,
    )
    if HEADLESS:
        return image_paths, [p for p in image_paths]
    images = []
    for p in image_paths:
        img = cv2.imread(p)
        if img is None:
            continue
        images.append(imutils.resize(img, width=STITCH_RESIZE_WIDTH))
    return image_paths, images


def stitch_images(progress_cb=None):
    def report(msg):
        if progress_cb:
            try:
                progress_cb(msg)
            except Exception:
                pass

    report("Loading images")
    image_paths, images = load_images_for_stitching()
    if len(images) < 2:
        raise ValueError("Need at least 2 images to stitch.")

    if HEADLESS:
        report("Building synthetic panorama")
        pil_imgs = [Image.open(p).convert("RGB") for p in image_paths]
        target_h = 400
        resized = []
        for im in pil_imgs:
            w = int(im.width * target_h / im.height)
            resized.append(im.resize((w, target_h), Image.LANCZOS))
        total_w = sum(im.width for im in resized)
        pano = Image.new("RGB", (total_w, target_h), (0, 0, 0))
        x = 0
        for im in resized:
            pano.paste(im, (x, 0))
            x += im.width
        pano.save(RAW_STITCHED_OUTPUT)
        bordered = Image.new("RGB", (pano.width + 20, pano.height + 20), (0, 0, 0))
        bordered.paste(pano, (10, 10))
        bordered.save(STITCHED_OUTPUT)
        report("Stitch complete")
        return True

    report("Stitching...")
    stitcher = cv2.Stitcher_create(cv2.Stitcher_PANORAMA)
    stitcher.setRegistrationResol(0.7)       # original 0.8 worked; 0.7 is a small speed gain
    stitcher.setSeamEstimationResol(0.1)
    stitcher.setCompositingResol(-1)
    stitcher.setPanoConfidenceThresh(0.4)
    stitcher.setWaveCorrection(False)        # servo sweep stays level; no benefit, saves time
    try:
        status, stitched = stitcher.stitch(images)
    except cv2.error as e:
        print("OpenCV crash during stitch:", e)
        if progress_cb:
            progress_cb("Stitch failed: OpenCV error")
        return False

    if status != cv2.Stitcher_OK:
        reasons = {
            cv2.Stitcher_ERR_NEED_MORE_IMGS:            "not enough matching images",
            cv2.Stitcher_ERR_HOMOGRAPHY_EST_FAIL:       "alignment failed — try better lighting",
            cv2.Stitcher_ERR_CAMERA_PARAMS_ADJUST_FAIL: "camera params failed — too much motion?",
        }
        reason = reasons.get(status, f"unknown error (code {status})")
        if progress_cb:
            progress_cb(f"Stitch failed: {reason}")
        return False

    report("Cropping black borders")
    cv2.imwrite(RAW_STITCHED_OUTPUT, stitched)
    stitched = cv2.copyMakeBorder(
        stitched, 10, 10, 10, 10, cv2.BORDER_CONSTANT, (0, 0, 0)
    )
    cleaned = crop_only_outer_black(stitched)
    cv2.imwrite(STITCHED_OUTPUT, cleaned)
    report("Stitch complete")
    return True


# ----------------------------------------------------------------------------
# Thermal printing
# Taken from perfect_stitch.py - unchanged output pipeline so the printed
# panoramas keep their tuned look. Logo is printed before AND after the pano.
# ----------------------------------------------------------------------------

def make_thermal_print_image(img):
    img = img.convert("L").rotate(90, expand=True)
    ratio = PRINTER_WIDTH / img.width
    img = img.resize((PRINTER_WIDTH, int(img.height * ratio)), Image.LANCZOS)

    arr = np.array(img).astype(np.float32) / 255.0
    arr = np.power(arr, PRINT_GAMMA)

    highlight_threshold = 0.78
    highlights = arr > highlight_threshold
    arr[highlights] = arr[highlights] - HIGHLIGHT_STRENGTH * (
        arr[highlights] - highlight_threshold
    )
    arr = np.clip(arr, 0.0, WHITE_MAX_LEVEL)
    arr_8 = (arr * 255).astype(np.uint8)

    clahe = cv2.createCLAHE(clipLimit=CLAHE_CLIP_LIMIT, tileGridSize=(8, 8))
    arr_8 = clahe.apply(arr_8)

    blur = cv2.GaussianBlur(arr_8, (0, 0), 1.0)
    arr_8 = cv2.addWeighted(arr_8, 1.45, blur, -0.45, 0)

    arr_f = arr_8.astype(np.float32)
    bright = arr_f > 205
    noise = np.random.normal(0, BRIGHT_NOISE_AMOUNT, size=arr_f.shape)
    arr_f[bright] = arr_f[bright] + noise[bright]
    arr_f = np.clip(arr_f, 0, 235)

    final = Image.fromarray(arr_f.astype(np.uint8)).convert("L")
    return final.convert("1", dither=Image.Dither.FLOYDSTEINBERG)


def make_logo_print_image(filepath):
    logo = Image.open(filepath).convert("L")
    if ROTATE_LOGO_VERTICAL:
        logo = logo.rotate(LOGO_ROTATION_DEGREES, expand=True)
        target_w = int(PRINTER_WIDTH * LOGO_VERTICAL_WIDTH_SCALE)
    else:
        target_w = int(PRINTER_WIDTH * LOGO_HORIZONTAL_WIDTH_SCALE)
    ratio = target_w / logo.width
    target_h = int(logo.height * ratio)
    logo = logo.resize((target_w, target_h), Image.LANCZOS)

    canvas_h = target_h + LOGO_PADDING_PX * 2
    canvas = Image.new("L", (PRINTER_WIDTH, canvas_h), 255)
    canvas.paste(logo, ((PRINTER_WIDTH - target_w) // 2, LOGO_PADDING_PX))

    canvas = ImageEnhance.Contrast(canvas).enhance(2.5)
    canvas = ImageEnhance.Sharpness(canvas).enhance(2.0)
    canvas = canvas.convert("1", dither=Image.Dither.NONE)

    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out = os.path.join(SAVE_DIR, f"logo_print_ready_{ts}.png")
    canvas.save(out)
    return out


def process_and_print(filepath, copies=1, progress_cb=None):
    def report(msg):
        if progress_cb:
            try:
                progress_cb(msg)
            except Exception:
                pass

    if HEADLESS:
        report("Simulated print")
        time.sleep(0.4 * max(1, copies))
        return True

    img = Image.open(filepath)
    try:
        exif = img._getexif()
        if exif:
            for tag, value in exif.items():
                if ExifTags.TAGS.get(tag) == "Orientation":
                    if value == 3:
                        img = img.rotate(180, expand=True)
                    elif value == 6:
                        img = img.rotate(270, expand=True)
                    elif value == 8:
                        img = img.rotate(90, expand=True)
                    break
    except Exception:
        pass

    report("Preparing panorama for printer")
    final = make_thermal_print_image(img)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    temp_path = os.path.join(SAVE_DIR, f"panorama_print_ready_{ts}.png")
    final.save(temp_path)

    logo_temp = None
    if PRINT_LOGO_AT_END and os.path.exists(LOGO_PATH):
        report("Preparing logo")
        logo_temp = make_logo_print_image(LOGO_PATH)

    p = None
    try:
        p = Usb(PRINTER_VID, PRINTER_PID)
        for n in range(copies):
            report(f"Printing copy {n + 1} of {copies}")
            p.hw("INIT")
            if logo_temp:
                p.image(logo_temp)
            p.image(temp_path)
            if logo_temp:
                p.image(logo_temp)
            p.cut()
        return True
    except Exception as e:
        print("Print failed:", e)
        # Re-raise so the UI shows the real error, not a generic message.
        raise
    finally:
        # Always release the USB interface so the next print run can claim it.
        if p is not None:
            try:
                p.close()
            except Exception:
                pass


# ----------------------------------------------------------------------------
# LED ring controller
# Taken from duen_hardware.py. Same daemon-thread render loop with a
# priority-flash hook for the camera flash and countdown ticks.
# ----------------------------------------------------------------------------

class LightController:
    def __init__(self, strip):
        self.strip = strip
        self.mode = "off"
        self.brightness = LED_DEFAULT_BRIGHTNESS
        self._running = True
        self._lock = threading.Lock()
        self._flash_req  = threading.Event()
        self._flash_done = threading.Event()
        self._flash_frac = 1.0
        self._flash_dur  = 0.10
        self._t = threading.Thread(target=self._loop, daemon=True)
        self._t.start()

    def set_mode(self, mode):
        if mode not in ("off", "white", "warm", "red", "disco", "snake"):
            return
        with self._lock:
            self.mode = mode

    def set_brightness(self, value):
        with self._lock:
            self.brightness = int(max(0, min(255, value)))
            self.strip.setBrightness(self.brightness)

    def flash_once(self, brightness_fraction=1.0, duration_s=0.10):
        self._flash_frac = brightness_fraction
        self._flash_dur  = duration_s
        self._flash_done.clear()
        self._flash_req.set()
        self._flash_done.wait(timeout=2.0)

    def stop(self):
        self._running = False
        try:
            for i in range(self.strip.numPixels()):
                self.strip.setPixelColor(i, Color(0, 0, 0))
            self.strip.show()
        except Exception:
            pass

    def _do_flash(self):
        with self._lock:
            current = self.brightness
        level = min(255, int(current * self._flash_frac))
        self.strip.setBrightness(level)
        for i in range(self.strip.numPixels()):
            self.strip.setPixelColor(i, Color(255, 255, 255))
        self.strip.show()
        time.sleep(self._flash_dur)
        for i in range(self.strip.numPixels()):
            self.strip.setPixelColor(i, Color(0, 0, 0))
        self.strip.show()
        with self._lock:
            self.strip.setBrightness(current)

    def _wheel(self, pos):
        pos = pos % 255
        if pos < 85:
            return Color(255 - pos * 3, 0, pos * 3)
        if pos < 170:
            pos -= 85
            return Color(0, pos * 3, 255 - pos * 3)
        pos -= 170
        return Color(pos * 3, 255 - pos * 3, 0)

    def _render_solid(self, rgb):
        r, g, b = rgb
        for i in range(self.strip.numPixels()):
            self.strip.setPixelColor(i, Color(r, g, b))

    def _render_off(self):
        for i in range(self.strip.numPixels()):
            self.strip.setPixelColor(i, Color(0, 0, 0))

    def _loop(self):
        head = 0
        j = 0
        while self._running:
            if self._flash_req.is_set():
                # try/finally guarantees _flash_done is always set even if the
                # LED hardware throws. Without this, flash_once() blocks for
                # its full 2-second timeout on every countdown tick, turning a
                # 5-second countdown into ~15 seconds.
                try:
                    self._do_flash()
                except Exception:
                    pass
                finally:
                    self._flash_req.clear()
                    self._flash_done.set()
                continue
            with self._lock:
                mode = self.mode
            try:
                if mode == "off":
                    self._render_off()
                    self.strip.show()
                    time.sleep(0.1)
                elif mode in LIGHT_COLORS:
                    self._render_solid(LIGHT_COLORS[mode])
                    self.strip.show()
                    time.sleep(0.1)
                elif mode == "disco":
                    n = self.strip.numPixels()
                    for i in range(n):
                        self.strip.setPixelColor(
                            i, self._wheel((i * 256 // n + j) & 255)
                        )
                    self.strip.show()
                    j = (j + 1) % 256
                    time.sleep(DISCO_STEP_MS / 1000.0)
                elif mode == "snake":
                    n = self.strip.numPixels()
                    for i in range(n):
                        self.strip.setPixelColor(i, Color(0, 0, 0))
                    for k in range(SNAKE_TAIL_LEN):
                        idx = (head - k) % n
                        falloff = (SNAKE_TAIL_LEN - k) / SNAKE_TAIL_LEN
                        v = int(255 * falloff)
                        self.strip.setPixelColor(idx, Color(v, v, v))
                    self.strip.show()
                    head = (head + 1) % n
                    time.sleep(SNAKE_STEP_MS / 1000.0)
                else:
                    time.sleep(0.1)
            except Exception:
                time.sleep(0.1)


# ----------------------------------------------------------------------------
# Print counter persistence
# ----------------------------------------------------------------------------

def load_print_total():
    try:
        with open(PRINT_COUNT_FILE) as f:
            return int(f.read().strip())
    except (OSError, ValueError):
        return 0


def save_print_total(n):
    tmp = PRINT_COUNT_FILE + ".tmp"
    try:
        with open(tmp, "w") as f:
            f.write(str(int(n)))
        os.replace(tmp, PRINT_COUNT_FILE)
    except OSError:
        pass


def cleanup_hardware():
    try:
        move_servo(135)
        time.sleep(0.1)
        pi.set_servo_pulsewidth(SERVO_PIN, 0)
    except Exception:
        pass
    try:
        camera.stop()
    except Exception:
        pass
    try:
        pi.stop()
    except Exception:
        pass


def _release_camera_for_subprocess():
    """Stop + close the global camera so duen_panorama.py can open it."""
    global camera
    if HEADLESS:
        return
    try:
        camera.stop()
    except Exception:
        pass
    try:
        camera.close()
    except Exception:
        pass


def _reinit_camera_after_subprocess():
    """Re-create the global camera with the full preview+still config."""
    global camera
    if HEADLESS:
        return
    camera = Picamera2()
    camera.configure(camera.create_still_configuration(
        main={"size": (IMAGE_WIDTH, IMAGE_HEIGHT)},
        lores={"size": (PREVIEW_W, PREVIEW_H), "format": "YUV420"},
        display="lores",
    ))
    camera.start()
    time.sleep(1.5)


def _hex_to_rgb(h):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


# ----------------------------------------------------------------------------
# Tkinter UI
# ----------------------------------------------------------------------------

class BoothApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("DUEN Photobooth")
        self.geometry(f"{WIN_W}x{WIN_H}")
        self.resizable(False, False)
        self.configure(bg=BG)
        # On the Pi, run fullscreen; in HEADLESS dev mode keep windowed.
        try:
            self.attributes("-fullscreen", not HEADLESS)
        except tk.TclError:
            pass

        # State
        self.lights = LightController(strip)
        self.mode = "panorama"
        self.countdown_secs = 3
        self.flash_state = "off"
        self.phase = "idle"
        self.pending_copies = 1
        self.session_count = 0
        self.total_count = load_print_total()
        self.stop_event = threading.Event()
        self.worker = None
        self.preview_active = True
        self.preview_fast = False
        self._countdown_value = None
        self.feed_img_ref = None
        self.thumb_img_ref = None
        self.logo_img_ref = None
        self.dot_widgets = []
        self.print_picker = None

        # GPIO buttons. when_pressed callbacks fire from a worker thread, so
        # every handler marshals back to the Tk main thread with after(0, ...).
        self._wire_gpio_buttons()

        # Build UI.
        self._build_ui()
        self._apply_phase("idle")
        self._update_flash_button()
        self._update_timer_button()
        self._update_mode_button()
        self._update_dots(active=-1, completed=-1)
        self._update_counters()

        # Bind Escape to close (handy on the Pi if a USB keyboard is plugged in).
        self.bind("<Escape>", lambda _e: self._on_close())
        self.protocol("WM_DELETE_WINDOW", self._on_close)

        self.after(50, self._tick_clock)
        self.after(120, self._preview_tick)

    # ------------------------------------------------------------------ UI BUILD
    def _build_ui(self):
        # Top bar
        self.top = tk.Frame(self, bg=SURFACE, height=36)
        self.top.place(x=0, y=0, width=WIN_W, height=36)

        # Logo or fallback "DUEN" text on the left
        logo_widget = None
        if os.path.exists(LOGO_PATH):
            try:
                im = Image.open(LOGO_PATH).convert("RGBA")
                im.thumbnail((120, 28), Image.LANCZOS)
                bg_im = Image.new("RGB", im.size, _hex_to_rgb(SURFACE))
                bg_im.paste(im, (0, 0), im)
                self.logo_img_ref = ImageTk.PhotoImage(bg_im)
                logo_widget = tk.Label(
                    self.top, image=self.logo_img_ref, bg=SURFACE
                )
            except Exception:
                logo_widget = None
        if logo_widget is None:
            logo_widget = tk.Label(
                self.top, text="DUEN", bg=SURFACE, fg=ACCENT2, font=F_MONO_LG
            )
        logo_widget.place(x=12, y=4)

        # Status pill (center)
        self.pill = tk.Label(
            self.top, text="READY", bg=GREEN, fg=BG,
            font=F_MONO_MD, padx=10, pady=2,
        )
        self.pill.place(x=WIN_W // 2, y=18, anchor="center")

        # Clock (right)
        self.clock = tk.Label(
            self.top, text="", bg=SURFACE, fg=TEXT, font=F_MONO_SM
        )
        self.clock.place(x=WIN_W - 8, y=10, anchor="ne")

        # Bottom bar
        self.bot = tk.Frame(self, bg=SURFACE, height=26)
        self.bot.place(x=0, y=WIN_H - 26, width=WIN_W, height=26)
        legend = (
            f"GPIO {SERVO_PIN}.Servo  "
            f"GPIO {LED_PIN}.Ring  "
            f"GPIO {CAPTURE_BTN_PIN}.Capture  "
            f"GPIO {FLASH_BTN_PIN}.Flash  "
            f"GPIO {PREVIEW_BTN_PIN}.Preview  "
            f"GPIO {ESTOP_BTN_PIN}.E-STOP"
        )
        tk.Label(
            self.bot, text=legend, bg=SURFACE, fg=MUTED, font=F_MONO_SM
        ).place(x=8, y=6)
        self.last_event_label = tk.Label(
            self.bot, text="", bg=SURFACE, fg=MUTED, font=F_MONO_SM
        )
        self.last_event_label.place(x=WIN_W - 8, y=6, anchor="ne")

        # Main area between bars
        main_y = 36
        main_h = WIN_H - 36 - 26   # 418

        # Feed canvas + caption
        feed_w = 560
        caption_h = 22
        self.feed = tk.Canvas(
            self, width=feed_w, height=main_h - caption_h,
            bg=BG, highlightthickness=0,
        )
        self.feed.place(x=0, y=main_y, width=feed_w, height=main_h - caption_h)
        self.feed_caption = tk.Label(
            self, text="Live feed", bg=SURFACE, fg=TEXT, font=F_MONO_SM,
            anchor="w",
        )
        self.feed_caption.place(
            x=0, y=main_y + (main_h - caption_h),
            width=feed_w, height=caption_h,
        )

        # Sidebar
        self.side = tk.Frame(self, bg=SURFACE)
        self.side.place(
            x=feed_w, y=main_y, width=WIN_W - feed_w, height=main_h
        )
        self._build_sidebar()

    def _build_sidebar(self):
        pad = 10
        sw = WIN_W - 560        # 240
        bar_w = sw - pad * 2    # 220
        y = 8

        tk.Label(
            self.side, text="STATUS", bg=SURFACE, fg=MUTED, font=F_MONO_SM
        ).place(x=pad, y=y)
        y += 14

        self.phase_label = tk.Label(
            self.side, text="idle", bg=SURFACE, fg=TEXT, font=F_PHASE
        )
        self.phase_label.place(x=pad, y=y)
        y += 26

        self.message_label = tk.Label(
            self.side, text="Press CAPTURE to begin",
            bg=SURFACE, fg=MUTED, font=F_SANS_SM,
            wraplength=bar_w, justify="left", anchor="w",
        )
        self.message_label.place(x=pad, y=y, width=bar_w, height=32)
        y += 36

        # Progress bar
        self.bar_track = tk.Frame(self.side, bg=BORDER, height=4)
        self.bar_track.place(x=pad, y=y, width=bar_w, height=4)
        self.bar_fill = tk.Frame(self.side, bg=ACCENT, height=4)
        self.bar_fill.place(x=pad, y=y, width=0, height=4)
        y += 8
        self.progress_label = tk.Label(
            self.side, text="", bg=SURFACE, fg=MUTED, font=F_MONO_SM
        )
        self.progress_label.place(x=pad, y=y)
        y += 14

        # 16-dot grid, 8 cols x 2 rows
        dot_size = 14
        dot_gap = 4
        cols = 8
        for i in range(len(ANGLES_TO_CAPTURE)):
            row = i // cols
            col = i % cols
            dx = pad + col * (dot_size + dot_gap)
            dy = y + row * (dot_size + dot_gap)
            dot = tk.Frame(self.side, bg=FAINT, width=dot_size, height=dot_size)
            dot.place(x=dx, y=dy, width=dot_size, height=dot_size)
            self.dot_widgets.append(dot)
        y += (dot_size + dot_gap) * 2 + 6

        # Sidebar buttons
        btn_h = 30
        self.start_btn = tk.Button(
            self.side, text="START PANORAMA",
            command=self._on_start_press,
            bg=ACCENT, fg="white",
            activebackground=ACCENT2, activeforeground="white",
            relief="flat", bd=0, font=F_MONO_MD,
        )
        self.start_btn.place(x=pad, y=y, width=bar_w, height=btn_h)
        y += btn_h + 6

        self.mode_btn = tk.Button(
            self.side, text="MODE: PANORAMA",
            command=self._on_mode_press,
            bg=SURFACE2, fg=TEXT,
            activebackground=ACCENT, activeforeground="white",
            relief="flat", bd=0, font=F_MONO_MD,
        )
        self.mode_btn.place(x=pad, y=y, width=bar_w, height=btn_h)
        y += btn_h + 6

        self.timer_btn = tk.Button(
            self.side, text="TIMER: 3s",
            command=self._on_timer_press,
            bg=SURFACE2, fg=TEXT,
            activebackground=ACCENT, activeforeground="white",
            relief="flat", bd=0, font=F_MONO_MD,
        )
        self.timer_btn.place(x=pad, y=y, width=bar_w, height=btn_h)
        y += btn_h + 6

        self.flash_btn = tk.Button(
            self.side, text="FLASH: OFF",
            command=self._on_flash_press,
            bg=SURFACE2, fg=TEXT,
            activebackground=ACCENT, activeforeground="white",
            relief="flat", bd=0, font=F_MONO_MD,
        )
        self.flash_btn.place(x=pad, y=y, width=bar_w, height=btn_h)
        y += btn_h + 10

        # Footer counters
        self.counter_label = tk.Label(
            self.side, text="", bg=SURFACE, fg=MUTED, font=F_MONO_SM,
            justify="left", anchor="w",
        )
        self.counter_label.place(x=pad, y=y, width=bar_w)

    # ------------------------------------------------------------------ GPIO
    def _wire_gpio_buttons(self):
        try:
            if HEADLESS:
                self._gpio_capture = Button(CAPTURE_BTN_PIN)
                self._gpio_flash   = Button(FLASH_BTN_PIN)
                self._gpio_preview = Button(PREVIEW_BTN_PIN)
                self._gpio_estop   = Button(ESTOP_BTN_PIN)
            else:
                self._gpio_capture = Button(CAPTURE_BTN_PIN, pull_up=True, bounce_time=0.05)
                self._gpio_flash   = Button(FLASH_BTN_PIN,   pull_up=True, bounce_time=0.05)
                self._gpio_preview = Button(PREVIEW_BTN_PIN, pull_up=True, bounce_time=0.05)
                self._gpio_estop   = Button(ESTOP_BTN_PIN,   pull_up=True, bounce_time=0.05)
            self._gpio_capture.when_pressed = lambda: self.after(0, self._on_start_press)
            self._gpio_flash.when_pressed   = lambda: self.after(0, self._on_flash_press)
            self._gpio_preview.when_pressed = lambda: self.after(0, self._on_preview_press)
            self._gpio_estop.when_pressed   = lambda: self.after(0, self._on_estop_press)
        except Exception as e:
            print("GPIO button wiring failed:", e)

    # ------------------------------------------------------------------ CLOCK
    def _tick_clock(self):
        self.clock.config(text=datetime.now().strftime("%a %b %d  %H:%M:%S"))
        self.after(1000, self._tick_clock)

    # ------------------------------------------------------------------ PHASE
    def _apply_phase(self, phase, message=None):
        self.phase = phase

        if phase == "idle":
            self.pill.config(text="READY", bg=GREEN, fg=BG)
            self.phase_label.config(text="idle", fg=TEXT)
            self.message_label.config(
                text=message or "Press CAPTURE to begin"
            )
            self._enable_buttons(True)
            self._update_dots(active=-1, completed=-1)
            self._set_progress(0, len(ANGLES_TO_CAPTURE), None, hide=True)
        elif phase == "countdown":
            self.pill.config(text="COUNTDOWN", bg=ACCENT2, fg=BG)
            self.phase_label.config(text="countdown", fg=ACCENT2)
            self.message_label.config(text=message or "Get ready...")
            self._enable_buttons(False)
        elif phase == "capturing":
            self.pill.config(text="RUNNING", bg=ACCENT, fg="white")
            self.phase_label.config(text="capturing", fg=ACCENT2)
            self.message_label.config(
                text=message or "Capturing panorama..."
            )
            self._enable_buttons(False)
        elif phase == "stitching":
            self.pill.config(text="STITCHING", bg=AMBER, fg=BG)
            self.phase_label.config(text="stitching", fg=AMBER)
            self.message_label.config(text=message or "Stitching images...")
            self._enable_buttons(False)
            self._set_dots_all(ACCENT)
            self._set_progress(0, len(ANGLES_TO_CAPTURE), None, hide=True)
        elif phase == "printing":
            self.pill.config(text="PRINTING", bg=ACCENT, fg="white")
            self.phase_label.config(text="printing", fg=ACCENT2)
            self.message_label.config(text=message or "Printing...")
            self._enable_buttons(False)
        elif phase == "done":
            self.pill.config(text="DONE", bg=GREEN, fg=BG)
            self.phase_label.config(text="done", fg=GREEN)
            self.message_label.config(text=message or "Print complete")
            self._enable_buttons(True)
            self._set_dots_all(GREEN)
        elif phase == "error":
            self.pill.config(text="ERROR", bg=RED, fg="white")
            self.phase_label.config(text="error", fg=RED)
            self.message_label.config(
                text=message or "Something went wrong"
            )
            self._enable_buttons(True)
            self._set_dots_all(FAINT)
            self._set_progress(0, len(ANGLES_TO_CAPTURE), None, hide=True)

    def _enable_buttons(self, on):
        state = tk.NORMAL if on else tk.DISABLED
        for b in (self.start_btn, self.mode_btn, self.timer_btn, self.flash_btn):
            try:
                b.config(state=state)
            except tk.TclError:
                pass

    def _set_progress(self, step, total, angle, hide=False):
        sw = WIN_W - 560
        bar_w = sw - 20
        if hide or total <= 0:
            self.bar_fill.place_configure(width=0)
            self.progress_label.config(text="")
            return
        frac = max(0.0, min(1.0, step / total))
        self.bar_fill.place_configure(width=int(bar_w * frac))
        if angle is None:
            self.progress_label.config(text=f"{step} / {total}")
        else:
            self.progress_label.config(text=f"{step} / {total}  {angle} deg")

    def _update_dots(self, active=-1, completed=-1):
        for i, dot in enumerate(self.dot_widgets):
            if completed >= 0 and i <= completed:
                dot.config(bg=ACCENT)
            elif active == i:
                dot.config(bg=ACCENT2)
            else:
                dot.config(bg=FAINT)

    def _set_dots_all(self, color):
        for d in self.dot_widgets:
            d.config(bg=color)

    def _update_counters(self):
        self.counter_label.config(
            text=f"Session: {self.session_count}\nTotal:   {self.total_count}"
        )

    def _update_flash_button(self):
        self.flash_btn.config(text=f"FLASH: {self.flash_state.upper()}")

    def _update_timer_button(self):
        if self.countdown_secs == 0:
            self.timer_btn.config(text="TIMER: OFF")
        else:
            self.timer_btn.config(text=f"TIMER: {self.countdown_secs}s")

    def _update_mode_button(self):
        self.mode_btn.config(text=f"MODE: {self.mode.upper()}")
        if self.mode == "still":
            self.start_btn.config(text="TAKE PHOTO")
        else:
            self.start_btn.config(text="START PANORAMA")

    # ----------------------------------------------------------- USER ACTIONS
    def _on_start_press(self):
        if self.phase not in ("idle", "done", "error"):
            return
        # Hide any leftover picker before starting a new run.
        self._close_picker()
        self.stop_event.clear()
        target = self._run_panorama if self.mode == "panorama" else self._run_single
        self.worker = threading.Thread(target=target, daemon=True)
        self.worker.start()

    def _on_mode_press(self):
        if self.phase not in ("idle", "done", "error"):
            return
        self.mode = "still" if self.mode == "panorama" else "panorama"
        self._update_mode_button()

    def _on_timer_press(self):
        if self.phase not in ("idle", "done", "error"):
            return
        try:
            idx = COUNTDOWN_CYCLE.index(self.countdown_secs)
        except ValueError:
            idx = 0
        self.countdown_secs = COUNTDOWN_CYCLE[(idx + 1) % len(COUNTDOWN_CYCLE)]
        self._update_timer_button()

    def _on_flash_press(self):
        # Flash cycle is always available so the user can stage the lights
        # while idle or even mid-countdown.
        try:
            idx = FLASH_CYCLE.index(self.flash_state)
        except ValueError:
            idx = 0
        self.flash_state = FLASH_CYCLE[(idx + 1) % len(FLASH_CYCLE)]
        self.lights.set_mode(self.flash_state)
        self._update_flash_button()

    def _on_preview_press(self):
        # Toggle live preview on/off. When off, the feed canvas shows a static
        # message; useful when staging subjects who don't want to see themselves.
        self.preview_active = not self.preview_active
        if not self.preview_active:
            self.feed.delete("preview")
            self.feed.delete("brackets")
            self.feed.delete("countdown")
            self.feed.create_text(
                280, 196, text="PREVIEW OFF", fill=MUTED,
                font=F_MONO_LG, tags="preview",
            )

    def _on_estop_press(self):
        if self.phase in ("idle", "done", "error"):
            return
        self.stop_event.set()
        self.message_label.config(text="EMERGENCY STOP requested")
        # Kill any active lighting immediately for safety.
        self.lights.set_mode("off")
        self.flash_state = "off"
        self._update_flash_button()

    # ---------------------------------------------------------------- PREVIEW
    def _preview_tick(self):
        # During active capture the feed shows each captured frame instead of
        # the live stream, so skip the preview update then.
        if self.preview_active and self.phase in ("idle", "countdown"):
            self._draw_preview_frame()
        delay_ms = max(40, int(1000 / PREVIEW_FPS_HZ))
        self.after(delay_ms, self._preview_tick)

    def _draw_preview_frame(self):
        try:
            if HEADLESS:
                img = self._synthetic_preview_image()
            else:
                arr = camera.capture_array("lores")
                if arr is None:
                    return
                # picamera2 lores in YUV420 -> stacked I420 layout.
                rgb = cv2.cvtColor(arr, cv2.COLOR_YUV2RGB_I420)
                img = Image.fromarray(rgb)
            cw = self.feed.winfo_width() or 560
            ch = self.feed.winfo_height() or 396
            img = img.resize((cw, ch), Image.LANCZOS)
            self.feed_img_ref = ImageTk.PhotoImage(img)
            self.feed.delete("preview")
            self.feed.create_image(
                0, 0, image=self.feed_img_ref, anchor="nw", tags="preview"
            )
            self._draw_corner_brackets()
            if self.phase == "countdown" and self._countdown_value is not None:
                self._draw_countdown_overlay(self._countdown_value)
        except Exception as e:
            print("preview err:", e)

    def _synthetic_preview_image(self):
        img = Image.new("RGB", (PREVIEW_W, PREVIEW_H), _hex_to_rgb(SURFACE2))
        d = ImageDraw.Draw(img)
        d.text((20, 20), "HEADLESS PREVIEW", fill=_hex_to_rgb(ACCENT2))
        d.text((20, 50), datetime.now().strftime("%H:%M:%S.%f")[:-3],
               fill=_hex_to_rgb(TEXT))
        d.text((20, 90), f"phase: {self.phase}", fill=_hex_to_rgb(MUTED))
        return img

    def _draw_corner_brackets(self):
        self.feed.delete("brackets")
        cw = self.feed.winfo_width() or 560
        ch = self.feed.winfo_height() or 396
        L = 22
        pad = 10
        w = 2
        c = ACCENT2
        corners = [
            (pad,      pad,      L,  0,  0,  L),
            (cw - pad, pad,     -L,  0,  0,  L),
            (pad,      ch - pad, L,  0,  0, -L),
            (cw - pad, ch - pad,-L,  0,  0, -L),
        ]
        for x, y, dx1, dy1, dx2, dy2 in corners:
            self.feed.create_line(
                x, y, x + dx1, y + dy1, fill=c, width=w, tags="brackets"
            )
            self.feed.create_line(
                x, y, x + dx2, y + dy2, fill=c, width=w, tags="brackets"
            )

    def _draw_countdown_overlay(self, value):
        self.feed.delete("countdown")
        cw = self.feed.winfo_width() or 560
        ch = self.feed.winfo_height() or 396
        cx, cy = cw // 2, ch // 2
        r = 80
        self.feed.create_oval(
            cx - r, cy - r, cx + r, cy + r,
            fill=BG, outline=ACCENT2, width=3, tags="countdown",
        )
        self.feed.create_text(
            cx, cy, text=str(value), fill="white",
            font=F_COUNT, tags="countdown",
        )

    # ------------------------------------------------------------ WORKFLOWS
    def _run_panorama(self):
        """
        Panorama capture+stitch runs as a subprocess (duen_panorama.py).
        The UI releases the camera before launching, reads STATUS/PROGRESS
        lines from the subprocess's stdout to drive the UI, then re-opens
        the camera once the subprocess exits. This isolates the OpenCV
        stitcher (which is the part that crashes/hangs) from the UI process.
        """
        try:
            self._ui(lambda: self._set_caption("Live feed"))
            self._ui(lambda: self._apply_phase("countdown"))
            if not self._do_countdown():
                self._ui(lambda: self._apply_phase("idle", "Cancelled"))
                return

            self._ui(lambda: self._apply_phase("capturing", "Releasing camera..."))

            # Stop preview and release camera so the subprocess can open it.
            self.preview_active = False
            _release_camera_for_subprocess()
            time.sleep(0.5)

            script_path = os.path.join(BASE_DIR, "duen_panorama.py")
            self._ui(lambda: self.message_label.config(text="Launching panorama..."))

            try:
                proc = subprocess.Popen(
                    [sys.executable, "-u", script_path],
                    stdout=subprocess.PIPE,
                    stderr=subprocess.PIPE,
                    text=True,
                    bufsize=1,
                    cwd=BASE_DIR,
                )
            except Exception as e:
                _reinit_camera_after_subprocess()
                self.preview_active = True
                self._ui(lambda err=str(e):
                         self._apply_phase("error", f"Could not launch: {err}"))
                return

            # Stream subprocess output, parsing the protocol lines.
            success = False
            fail_reason = "subprocess ended without OK/FAIL"
            total = len(ANGLES_TO_CAPTURE)

            sel = selectors.DefaultSelector()
            sel.register(proc.stdout, selectors.EVENT_READ)

            start_time = time.time()
            MAX_RUNTIME_S = 240   # 4 min watchdog — kills hung stitcher

            try:
                while True:
                    # Watchdog
                    if time.time() - start_time > MAX_RUNTIME_S:
                        proc.kill()
                        fail_reason = f"Timed out after {MAX_RUNTIME_S}s"
                        break

                    # User pressed E-stop
                    if self._check_stop():
                        proc.terminate()
                        try:
                            proc.wait(timeout=3)
                        except subprocess.TimeoutExpired:
                            proc.kill()
                        fail_reason = "Cancelled"
                        break

                    events = sel.select(timeout=0.5)

                    # Subprocess exited and no buffered output left?
                    if proc.poll() is not None and not events:
                        break

                    for key, _ in events:
                        line = key.fileobj.readline()
                        if not line:
                            continue
                        line = line.rstrip()
                        if not line:
                            continue

                        if line.startswith("STATUS:"):
                            msg = line[7:]
                            self._ui(lambda m=msg:
                                     self.message_label.config(text=m))
                        elif line.startswith("PROGRESS:"):
                            try:
                                i_str, total_str = line[9:].split("/", 1)
                                i = int(i_str)
                                tot = int(total_str)
                                idx = i - 1
                                if 0 <= idx < len(ANGLES_TO_CAPTURE):
                                    angle = ANGLES_TO_CAPTURE[idx]
                                else:
                                    angle = 0
                                self._ui(lambda ii=idx, aa=angle:
                                         self._update_capture_ui(ii, aa))
                                jpg = os.path.join(
                                    UNSTITCHED_FOLDER,
                                    f"{idx:02d}_angle_{angle:03d}.jpg"
                                )
                                if os.path.exists(jpg):
                                    self._ui(lambda p=jpg:
                                             self._show_captured_frame(p))
                                if i >= tot:
                                    self._ui(lambda: self._apply_phase(
                                        "stitching", "Stitching..."))
                            except Exception as parse_err:
                                print("PROGRESS parse error:", parse_err)
                        elif line == "OK":
                            success = True
                        elif line.startswith("FAIL:"):
                            fail_reason = line[5:]
                            success = False
                        else:
                            print("[panorama]:", line)

                # Drain stderr for the log
                try:
                    err_out = proc.stderr.read()
                    if err_out:
                        print("[panorama stderr]:", err_out.strip())
                except Exception:
                    pass
            finally:
                try:
                    sel.close()
                except Exception:
                    pass

            # Re-acquire camera before we touch anything UI-side.
            _reinit_camera_after_subprocess()
            self.preview_active = True

            if not success:
                self._ui(lambda r=fail_reason:
                         self._apply_phase("error", f"Stitch failed: {r}"))
                return

            if self._check_stop():
                return

            self._ui(self._show_stitched_in_feed)
            self.session_count += 1
            self._ui(self._update_counters)
            self._ui(self._show_print_picker)

        except Exception as e:
            print("Panorama error:", e)
            self._ui(lambda err=str(e): self._apply_phase("error", err))
            try:
                _reinit_camera_after_subprocess()
            except Exception:
                pass
            self.preview_active = True

    def _run_single(self):
        try:
            self._ui(lambda: self._apply_phase("countdown"))
            if not self._do_countdown():
                self._ui(lambda: self._apply_phase("idle", "Cancelled"))
                return

            self._ui(lambda: self._apply_phase("capturing", "Capturing photo..."))
            setup_image_folders()
            if self._check_stop():
                return

            # The preview lores stream keeps AE settled continuously, so no
            # warmup or discard capture is needed. Re-enable AE in case a
            # previous panorama run locked the exposure, then wait 0.3 s and
            # shoot immediately.
            camera.set_controls({"AeEnable": True, "AwbEnable": True})
            time.sleep(0.3)
            self.lights.flash_once(brightness_fraction=1.0, duration_s=0.08)
            path = take_picture(135, 0)

            # Route the single image through the same STITCHED_OUTPUT path so
            # process_and_print + the print picker stay identical to panorama.
            try:
                Image.open(path).save(STITCHED_OUTPUT)
            except Exception:
                shutil.copy(path, STITCHED_OUTPUT)
            try:
                subprocess.run(["sync"], check=False)
            except FileNotFoundError:
                pass
            if self._check_stop():
                return

            self._ui(self._show_stitched_in_feed)
            self.session_count += 1
            self._ui(self._update_counters)
            self._ui(self._show_print_picker)

        except Exception as e:
            print("Single error:", e)
            self._ui(lambda err=str(e): self._apply_phase("error", err))

    def _do_countdown(self):
        if self.countdown_secs <= 0:
            return True
        for v in range(self.countdown_secs, 0, -1):
            if self._check_stop():
                return False
            self._countdown_value = v
            # Quiet tick on the LED ring so the subject sees the rhythm.
            self.lights.flash_once(brightness_fraction=0.3, duration_s=0.1)
            time.sleep(1.0)
        self._countdown_value = None
        self._ui(lambda: self.feed.delete("countdown"))
        return True

    def _check_stop(self):
        if self.stop_event.is_set():
            self._ui(self._cleanup_after_stop)
            return True
        return False

    def _cleanup_after_stop(self):
        try:
            move_servo(135)
        except Exception:
            pass
        self.lights.set_mode("off")
        self.flash_state = "off"
        self._update_flash_button()
        self._countdown_value = None
        self.feed.delete("countdown")
        self._apply_phase("idle", "Stopped. Press CAPTURE to retry.")

    def _update_capture_ui(self, idx, angle):
        total = len(ANGLES_TO_CAPTURE)
        self._set_progress(idx + 1, total, angle)
        self._update_dots(active=idx, completed=idx - 1)
        self._set_caption(f"Shot {idx + 1} of {total}  .  {angle} deg")

    def _set_caption(self, text):
        self.feed_caption.config(text=text)

    def _stitch_progress_cb(self, msg):
        self._ui(lambda m=msg: self.message_label.config(text=m))

    def _show_captured_frame(self, path):
        """Display a just-captured JPEG on the feed canvas during the sweep."""
        try:
            im = Image.open(path).convert("RGB")
            cw = self.feed.winfo_width() or 560
            ch = self.feed.winfo_height() or 396
            im.thumbnail((cw, ch), Image.LANCZOS)
            self.feed_img_ref = ImageTk.PhotoImage(im)
            self.feed.delete("preview")
            self.feed.delete("brackets")
            self.feed.create_image(
                cw // 2, ch // 2, image=self.feed_img_ref,
                anchor="center", tags="preview",
            )
        except Exception as e:
            print("show captured frame err:", e)

    def _show_stitched_in_feed(self):
        try:
            im = Image.open(STITCHED_OUTPUT)
            cw = self.feed.winfo_width() or 560
            ch = self.feed.winfo_height() or 396
            im.thumbnail((cw, ch), Image.LANCZOS)
            self.feed_img_ref = ImageTk.PhotoImage(im)
            self.feed.delete("preview")
            self.feed.delete("brackets")
            self.feed.delete("countdown")
            self.feed.create_image(
                cw // 2, ch // 2, image=self.feed_img_ref,
                anchor="center", tags="preview",
            )
            self._set_caption("Panorama")
        except Exception as e:
            print("show stitched err:", e)

    # ---------------------------------------------------------- PRINT PICKER
    def _show_print_picker(self):
        self._apply_phase("done", "Choose how many copies to print")
        self._close_picker()

        ov_w = WIN_W - 60
        ov_h = WIN_H - 80
        ov = tk.Frame(
            self, bg=BG, bd=2, relief="flat",
            highlightthickness=2, highlightbackground=BORDER,
        )
        ov.place(x=30, y=40, width=ov_w, height=ov_h)
        self.print_picker = ov

        tk.Label(
            ov, text="PANORAMA READY", bg=BG, fg=ACCENT2, font=F_MONO_LG
        ).place(x=20, y=12)

        # Thumbnail
        try:
            im = Image.open(STITCHED_OUTPUT).convert("RGB")
            im.thumbnail((ov_w - 80, 170), Image.LANCZOS)
            self.thumb_img_ref = ImageTk.PhotoImage(im)
            tk.Label(
                ov, image=self.thumb_img_ref, bg=BG, bd=1, relief="solid"
            ).place(x=ov_w // 2, y=44, anchor="n")
        except Exception:
            tk.Label(
                ov, text="(thumbnail unavailable)",
                bg=BG, fg=MUTED, font=F_MONO_SM,
            ).place(x=ov_w // 2, y=120, anchor="n")

        # Copies row
        qy = 226
        tk.Label(
            ov, text="How many copies?", bg=BG, fg=TEXT, font=F_SANS_MD
        ).place(x=ov_w // 2, y=qy, anchor="n")
        self.pending_copies = 1
        cy = qy + 28

        self._copies_minus = tk.Button(
            ov, text="-", command=lambda: self._adjust_copies(-1),
            bg=SURFACE2, fg=TEXT,
            activebackground=ACCENT, activeforeground="white",
            relief="flat", bd=0, font=F_QTY_BTN,
        )
        self._copies_minus.place(x=ov_w // 2 - 90, y=cy, width=48, height=44, anchor="n")

        self.copies_label = tk.Label(
            ov, text="1", bg=BG, fg=TEXT, font=F_QTY, width=3
        )
        self.copies_label.place(x=ov_w // 2, y=cy - 4, anchor="n")

        self._copies_plus = tk.Button(
            ov, text="+", command=lambda: self._adjust_copies(+1),
            bg=SURFACE2, fg=TEXT,
            activebackground=ACCENT, activeforeground="white",
            relief="flat", bd=0, font=F_QTY_BTN,
        )
        self._copies_plus.place(x=ov_w // 2 + 90, y=cy, width=48, height=44, anchor="n")
        self._update_copies_buttons()

        # Action row
        ay = ov_h - 64
        bw = 130
        bh = 44

        tk.Button(
            ov, text="REDO", command=self._picker_redo,
            bg=SURFACE2, fg=TEXT,
            activebackground=ACCENT, activeforeground="white",
            relief="flat", bd=0, font=F_MONO_MD,
        ).place(x=20, y=ay, width=bw, height=bh)

        tk.Button(
            ov, text="PRINT", command=self._picker_print,
            bg=ACCENT, fg="white",
            activebackground=ACCENT2, activeforeground="white",
            relief="flat", bd=0, font=F_MONO_MD,
        ).place(x=ov_w // 2, y=ay, width=bw + 30, height=bh, anchor="n")

        tk.Button(
            ov, text="SKIP", command=self._picker_skip,
            bg=SURFACE2, fg=MUTED,
            activebackground=ACCENT, activeforeground="white",
            relief="flat", bd=0, font=F_MONO_MD,
        ).place(x=ov_w - 20, y=ay, width=bw, height=bh, anchor="ne")

    def _adjust_copies(self, delta):
        self.pending_copies = max(
            1, min(MAX_PRINTS_PER_RUN, self.pending_copies + delta)
        )
        self.copies_label.config(text=str(self.pending_copies))
        self._update_copies_buttons()

    def _update_copies_buttons(self):
        self._copies_minus.config(
            state=tk.NORMAL if self.pending_copies > 1 else tk.DISABLED
        )
        self._copies_plus.config(
            state=tk.NORMAL if self.pending_copies < MAX_PRINTS_PER_RUN
            else tk.DISABLED
        )

    def _close_picker(self):
        if self.print_picker is not None:
            try:
                self.print_picker.destroy()
            except Exception:
                pass
            self.print_picker = None

    def _picker_redo(self):
        self._close_picker()
        self._apply_phase("idle", "Starting over...")
        self.after(200, self._on_start_press)

    def _picker_skip(self):
        self._close_picker()
        self._apply_phase("idle", "Skipped. Ready for next capture.")

    def _picker_print(self):
        self._close_picker()
        copies = self.pending_copies
        threading.Thread(
            target=self._do_print, args=(copies,), daemon=True
        ).start()

    def _do_print(self, copies):
        try:
            self._ui(lambda c=copies: self._apply_phase(
                "printing", f"Printing {c} copy{'ies' if c != 1 else ''}..."
            ))
            ok = process_and_print(
                STITCHED_OUTPUT, copies=copies,
                progress_cb=self._stitch_progress_cb,
            )
            if not ok:
                self._ui(lambda: self._apply_phase("error", "Print failed."))
                return
            self.total_count += copies
            save_print_total(self.total_count)
            ts = datetime.now().strftime("%H:%M:%S")
            self._ui(lambda t=ts: self.last_event_label.config(
                text=f"Last print: {t}"))
            self._ui(self._update_counters)
            self._ui(lambda c=copies: self._apply_phase(
                "done", f"Printed {c}. Press CAPTURE for another."
            ))
        except Exception as e:
            self._ui(lambda err=str(e): self._apply_phase("error", err))

    # --------------------------------------------------------- THREAD HELPER
    def _ui(self, fn):
        """Marshal a callable from a worker thread onto the Tk main loop."""
        try:
            self.after(0, fn)
        except RuntimeError:
            # Window destroyed mid-call; silently drop.
            pass

    # -------------------------------------------------------------- SHUTDOWN
    def _on_close(self):
        try:
            self.stop_event.set()
            self.lights.stop()
            cleanup_hardware()
        finally:
            try:
                self.destroy()
            except Exception:
                pass


# ----------------------------------------------------------------------------
# Entry point
# ----------------------------------------------------------------------------

if __name__ == "__main__":
    app = None
    try:
        app = BoothApp()
        app.mainloop()
    except KeyboardInterrupt:
        print("\nInterrupted by user.")
    finally:
        try:
            cleanup_hardware()
        except Exception:
            pass


