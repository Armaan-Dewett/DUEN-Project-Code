# DUEN hardware logic: pigpio motor + camera + stitching + thermal printing + lights
# Keep this file separate from the UI so troubleshooting is easier.
# Run on Raspberry Pi before UI:
#   sudo pigpiod
# The UI imports this file as: import duen_hardware as hw


# GPIO 23   -   Capture
# GPIO 16   -   Customization Button
# GPIO 5    -   Emergency Button
# GPIO 24   -   Preview


import os, shutil, glob, subprocess, threading, time
from datetime import datetime
from PIL import Image, ImageEnhance, ExifTags, ImageDraw


# Optional DUEN post-processing filters. Keep the booth usable even if the
# file is missing during development. Put photo_filters.py beside this file.
try:
  import photo_filters
except Exception as _filter_import_error:
  photo_filters = None
  print(f"[filters] photo_filters.py not loaded: {_filter_import_error}")


# ── HARDWARE IMPORTS (with explicit headless fallback for dev machines) ──
# Default is REAL hardware mode. This preserves the behavior of the working
# pigpio panorama script instead of silently using fake hardware.
HEADLESS = os.environ.get("DUEN_HEADLESS", "0") == "1"


class _FakePi:
  connected = True
  def set_servo_pulsewidth(self, pin, us): pass
  def stop(self): pass


try:
  import pigpio
except ImportError as e:
  if not HEADLESS:
      raise RuntimeError("pigpio is required on the Raspberry Pi. Install it and run: sudo pigpiod") from e
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
      raise RuntimeError("cv2, numpy, and imutils are required for real stitching.") from e
  cv2 = None
  np = None
  imutils = None


try:
  from picamera2 import Picamera2
  from libcamera import Transform
except ImportError as e:
  if not HEADLESS:
      raise RuntimeError("picamera2 is required on the Raspberry Pi.") from e
  class Picamera2:
      def __init__(self): pass
      def create_still_configuration(self, **kw): return {}
      def configure(self, *a, **kw): pass
      def start(self): pass
      def stop(self): pass
      def set_controls(self, *a, **kw): pass
      def capture_metadata(self): return {"ExposureTime": EXPOSURE_TIME_US, "AnalogueGain": ANALOGUE_GAIN}
      def capture_file(self, path):
          _make_synthetic_image(path, label=os.path.basename(path))
      def capture_array(self, which="lores"):
          return None  # signal preview loop to use synthetic frame


try:
  from gpiozero import Button
except ImportError as e:
  if not HEADLESS:
      raise RuntimeError("gpiozero is required for the physical buttons.") from e
  class Button:
      def __init__(self, pin, **kw):
          self.pin = pin
          self.when_pressed = None


try:
  from escpos.printer import Usb
except ImportError as e:
  if not HEADLESS:
      raise RuntimeError("python-escpos is required for USB thermal printing.") from e
  class Usb:
      def __init__(self, *a, **kw): pass
      def hw(self, *a, **kw): pass
      def image(self, *a, **kw): pass
      def text(self, *a, **kw): pass
      def cut(self, *a, **kw): pass


try:
  from rpi_ws281x import PixelStrip, Color
except ImportError as e:
  if not HEADLESS:
      raise RuntimeError("rpi_ws281x is required for the LED ring.") from e
  def Color(r, g, b): return (int(r), int(g), int(b))
  class PixelStrip:
      def __init__(self, count, *a, **kw):
          self._n = count
          self._pixels = [(0, 0, 0)] * count
      def begin(self): pass
      def numPixels(self): return self._n
      def setBrightness(self, v): pass
      def setPixelColor(self, i, c):
          if 0 <= i < self._n:
              self._pixels[i] = c
      def show(self): pass


def _make_synthetic_image(path, label="", size=(1280, 720)):
  """Write a labelled synthetic JPEG so the UI has something to display."""
  # Hash label to a hue so successive frames look different.
  h = (sum(ord(c) for c in label) * 37) % 360
  # Convert HSV→RGB roughly.
  import colorsys
  r, g, b = colorsys.hsv_to_rgb(h / 360.0, 0.45, 0.6)
  bg = (int(r * 255), int(g * 255), int(b * 255))
  img = Image.new("RGB", size, bg)
  d = ImageDraw.Draw(img)
  d.rectangle([0, 0, size[0] - 1, size[1] - 1], outline=(255, 255, 255), width=4)
  d.text((20, 20), label or "synthetic", fill=(255, 255, 255))
  img.save(path, "JPEG", quality=85)


# ── PIN CONFIGURATION ─────────────────────────────────────────
SERVO_PIN           = 26
BUTTON_PIN          = 17
SETTINGS_BUTTON_PIN = 27


# ── FOLDER CONFIGURATION ──────────────────────────────────────
# Use absolute paths exactly like the standalone Perfect Stitch script.
# This prevents captures/output from landing in a random working directory
# when the UI is launched with sudo, from systemd, or from another folder.
BASE_DIR            = os.path.dirname(os.path.abspath(__file__))
SAVE_DIR            = os.path.expanduser("~/photos")
IMAGE_FOLDER        = os.path.join(BASE_DIR, "imageprinter")
UNSTITCHED_FOLDER   = os.path.join(IMAGE_FOLDER, "unstitchedImages")
STITCHED_OUTPUT     = os.path.join(IMAGE_FOLDER, "stitchedOutputProcessed.png")
RAW_STITCHED_OUTPUT = os.path.join(IMAGE_FOLDER, "stitchedOutputRaw.png")
STITCH_RESIZE_WIDTH = 1400
LOGO_PATH           = os.path.join(BASE_DIR, "duen_logo.png")
PRINT_COUNT_FILE    = os.path.join(SAVE_DIR, ".print_count")
MAX_PRINTS_PER_RUN  = 6
DEFAULT_PRINTS      = 1
COUNTDOWN_OPTIONS   = (0, 3, 5)
DEFAULT_COUNTDOWN   = 3


# ── OUTPUT FILTER / POST-PROCESSING CONFIGURATION ───────────
# UI-facing choices:
#   none     = Regular
#   sketch   = Pencil sketch
#   comic    = Comic/cartoon
#   pixel    = Retro/pixel
#   branding = Event title footer
FILTER_CHOICES = {
  "none":     "Regular",
  "sketch":   "Sketch",
  "comic":    "Comic",
  "pixel":    "Retro",
  "branding": "Event Title",
}
DEFAULT_FILTER_KEY  = "none"
DEFAULT_EVENT_TITLE = "PANORAMA EVENT"
CURRENT_FILTER_KEY  = DEFAULT_FILTER_KEY
CURRENT_EVENT_TITLE = DEFAULT_EVENT_TITLE


def normalize_filter_key(filter_key):
  key = (filter_key or DEFAULT_FILTER_KEY).strip().lower()
  aliases = {
      "regular": "none",
      "normal": "none",
      "no filter": "none",
      "none": "none",
      "retro": "pixel",
      "pixel / retro": "pixel",
      "pixel": "pixel",
      "event": "branding",
      "event title": "branding",
      "branding": "branding",
      "title": "branding",
      "sketch": "sketch",
      "pencil": "sketch",
      "comic": "comic",
      "cartoon": "comic",
  }
  return aliases.get(key, DEFAULT_FILTER_KEY)


def set_output_filter(filter_key=None, event_title=None):
  """Called by the UI when the user changes the customization filter."""
  global CURRENT_FILTER_KEY, CURRENT_EVENT_TITLE
  CURRENT_FILTER_KEY = normalize_filter_key(filter_key)
  if event_title is not None:
      cleaned = str(event_title).strip()
      CURRENT_EVENT_TITLE = cleaned or DEFAULT_EVENT_TITLE
  print(f"[filters] selected={CURRENT_FILTER_KEY}, event={CURRENT_EVENT_TITLE}")
  return CURRENT_FILTER_KEY


def apply_output_filter_to_file(input_path, output_path=None, filter_key=None, event_title=None):
  """
  Apply the selected DUEN post-processing filter to a completed output file.


  This is intentionally done AFTER capture/stitch/strip assembly, so the
  filters never interfere with OpenCV feature matching or camera exposure.
  Returns the saved path. If the filter is Regular or photo_filters.py is
  unavailable, the original file is returned unchanged unless output_path is
  explicitly provided.
  """
  key = normalize_filter_key(filter_key or CURRENT_FILTER_KEY)
  title = (event_title if event_title is not None else CURRENT_EVENT_TITLE)
  title = str(title or DEFAULT_EVENT_TITLE).strip() or DEFAULT_EVENT_TITLE


  if not input_path or not os.path.exists(input_path):
      return input_path


  # Regular mode: do not create another file unless a caller asks for one.
  if key == "none":
      if output_path:
          Image.open(input_path).convert("RGB").save(output_path)
          return output_path
      return input_path


  if photo_filters is None:
      print("[filters] requested filter but photo_filters.py is unavailable; using original image.")
      return input_path


  root, ext = os.path.splitext(input_path)
  if not ext:
      ext = ".png"
  if output_path is None:
      output_path = f"{root}_{key}{ext}"


  try:
      img = Image.open(input_path).convert("RGB")
      if key == "branding":
          filtered = photo_filters.apply_filter(
              img,
              "branding",
              event_subtitle=title,
          )
      else:
          filtered = photo_filters.apply_filter(img, key)
      filtered.save(output_path)
      print(f"[filters] saved {key} output: {output_path}")
      return output_path
  except Exception as e:
      print(f"[filters] failed to apply {key}: {e}")
      return input_path


# Logo settings copied from the working pigpio panorama script.
PRINT_LOGO_AT_END        = True
ROTATE_LOGO_VERTICAL     = False
LOGO_ROTATION_DEGREES    = 270
LOGO_VERTICAL_WIDTH_SCALE = 0.55
LOGO_HORIZONTAL_WIDTH_SCALE = 0.50
LOGO_PADDING_PX          = 40


# ── SPEED + QUALITY SETTINGS FROM THE WORKING PIGPIO SCRIPT ──
STITCH_RESIZE_WIDTH = 1400
FAST_SERVO_WAIT     = 0.28
FIRST_CAPTURE_WAIT  = 1.00
FIRST_CAPTURE_FLUSH_WAIT = 0.20
POST_CAPTURE_WAIT   = 0.20
IMAGE_WIDTH         = 1920
IMAGE_HEIGHT        = 1920
STILL_WIDTH         = 3280
STILL_HEIGHT        = 2464


# EXPOSURE CALIBRATION!!!!!!!!!!!!!!!


EXPOSURE_TIME_US    = 6000
ANALOGUE_GAIN       = 3.0
USE_AUTO_EXPOSURE   = True
EXPOSURE_CAP_US     = 20000
GAIN_CAP            = 10.0


# Thermal printer image settings from the working pigpio script.
PRINTER_WIDTH        = 384
WHITE_MAX_LEVEL      = 0.88
HIGHLIGHT_STRENGTH   = 0.18
PRINT_GAMMA          = 0.85
CLAHE_CLIP_LIMIT     = 1.2
BRIGHT_NOISE_AMOUNT  = 4


# 20-image sweep for better overlap and more reliable stitching.
# Smaller angle jumps make OpenCV matching much easier than the old 16-image sweep.
ANGLES_TO_CAPTURE = [
  265, 247, 229, 211, 193, 175, 157, 139,
  121, 103, 88, 73, 58, 43, 28, 13
]


MIN_PULSE   = 500
MAX_PULSE   = 2500
MAX_DEGREES = 270


PREVIEW_W              = 640
PREVIEW_H              = 480
PREVIEW_FPS_HZ         = 15
PREVIEW_PAN_DURATION_S = 10.0
PREVIEW_PAN_START      = 0
PREVIEW_PAN_END        = 270
PREVIEW_PAN_STEPS      = 20


# ── LED RING CONFIGURATION ────────────────────────────────────
LED_COUNT              = 61
LED_PIN                = 18
LED_FREQ_HZ            = 800000
LED_DMA                = 10
LED_INVERT             = False
LED_CHANNEL            = 0
LED_DEFAULT_BRIGHTNESS = 128
SNAKE_TAIL_LEN         = 6
SNAKE_STEP_MS          = 40
DISCO_STEP_MS          = 20
LIGHT_MODES            = ("white", "warm", "red", "disco", "snake")
DEFAULT_MODE           = "white"
LIGHT_COLORS           = {
  "white": (255, 255, 255),
  "warm":  (255, 170,  60),
  "red":   (255,   0,   0),
}


# ── CAMERA THREAD SAFETY ──────────────────────────────────────
# The UI preview thread and the capture/stitch thread both touch Picamera2.
# Every camera operation must go through this lock so preview cannot overlap
# a still capture or exposure lock.
CAMERA_LOCK = threading.RLock()


# ── SETUP ─────────────────────────────────────────────────────
os.makedirs(SAVE_DIR, exist_ok=True)


pi = pigpio.pi()
if not pi.connected:
  if not HEADLESS:
      raise RuntimeError("Could not connect to pigpiod. Run: sudo pigpiod")
  pi = _FakePi()


if HEADLESS:
  button          = Button(BUTTON_PIN)
  settings_button = Button(SETTINGS_BUTTON_PIN)
  class _FakeCamera:
      def create_still_configuration(self, **kw): return {}
      def configure(self, *a, **kw): pass
      def start(self): pass
      def stop(self): pass
      def set_controls(self, *a, **kw): pass
      def capture_metadata(self): return {"ExposureTime": EXPOSURE_TIME_US, "AnalogueGain": ANALOGUE_GAIN}
      def capture_file(self, path):
          _make_synthetic_image(path, label=os.path.basename(path))
      def capture_array(self, which="lores"): return None
  camera = _FakeCamera()
  strip  = PixelStrip(LED_COUNT, LED_PIN, LED_FREQ_HZ,
                      LED_DMA, LED_INVERT, 0, LED_CHANNEL)
  strip.begin()
else:
  button          = Button(BUTTON_PIN, pull_up=True, bounce_time=0.05)
  settings_button = Button(SETTINGS_BUTTON_PIN, pull_up=True, bounce_time=0.05)
  camera = Picamera2()
  camera.configure(camera.create_still_configuration(
      main={"size": (IMAGE_WIDTH, IMAGE_HEIGHT)},
      lores={"size": (PREVIEW_W, PREVIEW_H), "format": "YUV420"},
      display="lores",
  ))
  camera.start()
  # Match the standalone working script: give the camera pipeline time to fully start.
  time.sleep(4)
  strip = PixelStrip(LED_COUNT, LED_PIN, LED_FREQ_HZ,
                     LED_DMA, LED_INVERT, LED_DEFAULT_BRIGHTNESS, LED_CHANNEL)
  strip.begin()


# ── SERVO HELPERS ─────────────────────────────────────────────
def angle_to_pulse(angle):
  angle = max(0, min(MAX_DEGREES, angle))
  return int(MIN_PULSE + (angle / MAX_DEGREES) * (MAX_PULSE - MIN_PULSE))


def move_servo(angle):
  if not getattr(pi, "connected", True):
      raise RuntimeError("Lost connection to pigpiod during sweep!")
  pi.set_servo_pulsewidth(SERVO_PIN, angle_to_pulse(angle))


# ── PERSISTENT PRINT COUNTER ──────────────────────────────────
def load_print_total():
  try:
      with open(PRINT_COUNT_FILE, "r") as f:
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


# ── IMAGE CAPTURE + PIGPIO PANORAMA PIPELINE ────────────────
def setup_image_folders():
  print("Preparing image folders...")
  try:
      if os.path.exists(IMAGE_FOLDER):
          shutil.rmtree(IMAGE_FOLDER)
      os.makedirs(UNSTITCHED_FOLDER, exist_ok=True)
  except PermissionError:
      print("\nPermission error while preparing image folders.")
      print(f"Could not write inside: {BASE_DIR}")
      print("Fix with:")
      print(f"sudo rm -rf {IMAGE_FOLDER}")
      print(f"sudo chown -R $USER:$USER {BASE_DIR}")
      print(f"chmod -R u+rwX {BASE_DIR}")
      raise
  print(f"Image folder ready: {UNSTITCHED_FOLDER}")


def safe_capture_metadata():
  """Read camera metadata without forcing a full image capture."""
  if HEADLESS:
      return {"ExposureTime": EXPOSURE_TIME_US, "AnalogueGain": ANALOGUE_GAIN}
  with CAMERA_LOCK:
      try:
          return camera.capture_metadata()
      except Exception as e:
          print(f"Metadata read failed once: {e}")
          time.sleep(0.3)
          return camera.capture_metadata()


def warmup_camera():
  """
  Warm up exposure from the center of the sweep.


  Important:
  This version does not call camera.capture_array().
  capture_array() can stress the camera pipeline before the real captures.
  """


  print("Warming up camera...")


  move_servo(135)
  time.sleep(0.7)


  with CAMERA_LOCK:
      camera.set_controls({
          "AeEnable": True,
          "AwbEnable": True,
      })


  # Let auto exposure settle without forcing full frame captures.
  for _ in range(5):
      time.sleep(0.25)
      safe_capture_metadata()


  metadata = safe_capture_metadata()


  if USE_AUTO_EXPOSURE:
      locked_exposure = min(metadata.get("ExposureTime", EXPOSURE_TIME_US), EXPOSURE_CAP_US)
      locked_gain     = min(metadata.get("AnalogueGain",  ANALOGUE_GAIN),   GAIN_CAP)
  else:
      locked_exposure = EXPOSURE_TIME_US
      locked_gain     = ANALOGUE_GAIN


  print(f"Exposure locked: {locked_exposure} us, gain {locked_gain:.2f}")


  with CAMERA_LOCK:
      camera.set_controls({
          "AeEnable": False,
          "ExposureTime": locked_exposure,
          "AnalogueGain": locked_gain,
          "AwbEnable": True,
      })
  time.sleep(0.4)


def flush_camera_once():
  """
  Captures one throwaway frame after the big first movement.


  This matches Perfect Stitch. It prevents the first saved image from being a stale,
  blurry, or unstable frame right after the chassis makes the large jump from the
  warmup angle to the first capture angle.
  """
  temp_path = os.path.join(UNSTITCHED_FOLDER, "_throwaway_first_frame.jpg")


  try:
      print("Taking throwaway frame...")
      if HEADLESS:
          _make_synthetic_image(temp_path, label="throwaway")
      else:
          with CAMERA_LOCK:
              camera.capture_file(temp_path)


      if os.path.exists(temp_path):
          os.remove(temp_path)


      time.sleep(FIRST_CAPTURE_FLUSH_WAIT)


  except Exception as e:
      print(f"Throwaway frame failed, continuing anyway: {e}")


def take_picture(angle, index):
  filename = os.path.join(
      UNSTITCHED_FOLDER,
      f"{index:02d}_angle_{angle:03d}.jpg"
  )


  if HEADLESS:
      _make_synthetic_image(filename, label=f"{index:02d}  {angle}°")
      time.sleep(POST_CAPTURE_WAIT)
      return filename


  try:
      print(f"Saving photo to: {filename}")
      with CAMERA_LOCK:
          camera.capture_file(filename)
      time.sleep(POST_CAPTURE_WAIT)
      return filename


  except PermissionError:
      print("\nPermission denied while saving image.")
      print(f"Could not write to: {filename}")
      print("Fix with:")
      print(f"sudo rm -rf {IMAGE_FOLDER}")
      print(f"sudo chown -R $USER:$USER {BASE_DIR}")
      print(f"chmod -R u+rwX {BASE_DIR}")
      raise


  except Exception as e:
      print(f"Capture failed at angle {angle}: {e}")
      time.sleep(0.5)


      # One retry.
      print("Retrying capture...")
      with CAMERA_LOCK:
          camera.capture_file(filename)
      time.sleep(POST_CAPTURE_WAIT)
      return filename


def take_still_photo(filepath):
   """Single high-resolution capture for still photo mode."""
   if HEADLESS:
       _make_synthetic_image(filepath, label="still")
       return filepath


   # Temporarily reconfigure camera to full sensor resolution.
   with CAMERA_LOCK:
       camera.stop()
       camera.configure(camera.create_still_configuration(
           main={"size": (STILL_WIDTH, STILL_HEIGHT)},
       ))
       camera.start()
       time.sleep(1.0)   # let exposure settle at new config
       camera.capture_file(filepath)
       # Restore original config for preview and panorama use.
       camera.stop()
       camera.configure(camera.create_still_configuration(
           main={"size": (IMAGE_WIDTH, IMAGE_HEIGHT)},
           lores={"size": (PREVIEW_W, PREVIEW_H), "format": "YUV420"},
           display="lores",
       ))
       camera.start()
       time.sleep(1.0)


   return filepath


def make_photobooth_strip(image_paths, output_path):
   """
   Compose 4 still photos into a vertical photobooth strip for thermal printing.
   Photos fill the strip width � the height is cropped from centre so each
   photo is portrait-oriented (taller than wide). No rotation applied.
   DUEN logo at top and bottom.
   """
   STRIP_WIDTH  = PRINTER_WIDTH         # 384px � matches thermal printer
   PHOTO_HEIGHT = int(STRIP_WIDTH * 1.2) # portrait: taller than wide
   GAP          = 8                     # px between photos


   photos = []
   for path in image_paths:
       try:
           img = Image.open(path).convert("RGB")
           # Scale so the HEIGHT fills PHOTO_HEIGHT, then crop WIDTH to STRIP_WIDTH
           ratio    = PHOTO_HEIGHT / img.height
           new_w    = int(img.width * ratio)
           img      = img.resize((new_w, PHOTO_HEIGHT), Image.LANCZOS)
           # Centre-crop width to STRIP_WIDTH
           if new_w > STRIP_WIDTH:
               left = (new_w - STRIP_WIDTH) // 2
               img  = img.crop((left, 0, left + STRIP_WIDTH, PHOTO_HEIGHT))
           else:
               padded = Image.new("RGB", (STRIP_WIDTH, PHOTO_HEIGHT), (0, 0, 0))
               padded.paste(img, ((STRIP_WIDTH - new_w) // 2, 0))
               img = padded
           photos.append(img)
       except Exception as e:
           print(f"[strip] could not load {path}: {e}")
           photos.append(Image.new("RGB", (STRIP_WIDTH, PHOTO_HEIGHT), (20, 20, 20)))


   total_h = (len(photos) * PHOTO_HEIGHT
              + (len(photos) - 1) * GAP)


   strip = Image.new("RGB", (STRIP_WIDTH, total_h), (255, 255, 255))


   y = 0
   for i, photo in enumerate(photos):
       strip.paste(photo, (0, y))
       y += PHOTO_HEIGHT
       if i < len(photos) - 1:
           y += GAP


   # Pre-rotate 270 degrees to counteract the 90-degree rotation
   # applied inside make_thermal_print_image for all print jobs.
   strip = strip.rotate(270, expand=True)
   strip.save(output_path)
   print(f"[strip] saved: {output_path}")
   return output_path


# ============================================================
# PANORAMA STITCHING
# ============================================================


def extract_capture_index(path):
  filename = os.path.basename(path)
  return int(filename.split("_")[0])


def crop_only_outer_black(stitched_img):
  """
  Finds the largest rectangle containing zero black pixels.
  Uses the histogram/stack algorithm on each row, which gives the
  true maximum interior rectangle with minimum crop and no black remnants.
  """


  gray = cv2.cvtColor(stitched_img, cv2.COLOR_BGR2GRAY)


  # Build a binary mask: 1 = non-black, 0 = black.
  # Threshold of 10 absorbs JPEG compression noise around true-black edges.
  mask = (gray > 10).astype(np.uint8)


  if mask.sum() == 0:
      print("No visible pixels found. Saving raw stitched image.")
      return stitched_img


  rows, cols = mask.shape


  # heights[x] = number of consecutive non-black pixels ending at this row.
  heights = np.zeros(cols, dtype=np.int32)


  best_area = 0
  best_rect = (0, 0, rows, cols)


  for row in range(rows):
      # Extend or reset each column's height.
      heights = np.where(mask[row] == 1, heights + 1, 0)


      # Largest rectangle in histogram for this row.
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
  print(
      f"Largest interior rectangle: ({x1},{y1}) -> ({x2},{y2}), "
      f"size {x2 - x1}x{y2 - y1}, area {best_area}"
  )


  return stitched_img[y1:y2, x1:x2]


def load_images_for_stitching():
  image_paths = sorted(
      glob.glob(os.path.join(UNSTITCHED_FOLDER, "*.jpg")),
      key=extract_capture_index
  )


  if HEADLESS:
      return image_paths, [p for p in image_paths]


  images = []


  print("\nImages being loaded for stitching:")


  for image_path in image_paths:
      print("Loading:", os.path.basename(image_path))


      img = cv2.imread(image_path)


      if img is None:
          print("Could not read:", image_path)
          continue


      img_stitch = imutils.resize(img, width=STITCH_RESIZE_WIDTH)
      images.append(img_stitch)


  return image_paths, images


def stitch_images():
  image_paths, images = load_images_for_stitching()


  print(f"\nStitching {len(images)} images...")


  if len(images) < 2:
      raise ValueError("Need at least 2 images to stitch.")


  if HEADLESS:
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
      return True


  imageStitcher = cv2.Stitcher_create(cv2.Stitcher_PANORAMA)


  # Stable settings for servo panoramas.
  imageStitcher.setRegistrationResol(0.8)
  imageStitcher.setSeamEstimationResol(0.1)
  imageStitcher.setCompositingResol(-1)


  try:
      status, stitched_img = imageStitcher.stitch(images)


  except cv2.error as e:
      print("OpenCV crashed during stitching.")
      print("This usually means weak feature matches or unstable geometry.")
      print("Error:")
      print(e)
      return False


  if status == cv2.Stitcher_OK:
      print("Stitching complete.")


      cv2.imwrite(RAW_STITCHED_OUTPUT, stitched_img)


      stitched_img = cv2.copyMakeBorder(
          stitched_img,
          10,
          10,
          10,
          10,
          cv2.BORDER_CONSTANT,
          (0, 0, 0)
      )


      cleaned_img = crop_only_outer_black(stitched_img)


      cv2.imwrite(STITCHED_OUTPUT, cleaned_img)


      print(f"Saved raw stitched image: {RAW_STITCHED_OUTPUT}")
      print(f"Saved final image: {STITCHED_OUTPUT}")


      return True


  print("Stitching failed. Status code:", status)


  if status == cv2.Stitcher_ERR_NEED_MORE_IMGS:
      print("Reason: Not enough usable matching images.")
      print("The images may overlap visually, but OpenCV could not find enough matching features.")


  elif status == cv2.Stitcher_ERR_HOMOGRAPHY_EST_FAIL:
      print("Reason: Images could not align.")
      print("Try better lighting, more overlap, or more servo settle time.")


  elif status == cv2.Stitcher_ERR_CAMERA_PARAMS_ADJUST_FAIL:
      print("Reason: Camera parameter adjustment failed.")
      print("Most likely cause: weak matches or unstable stitch geometry.")


  return False


# ============================================================
# OPTIONAL DEBUG DIAGNOSIS
# ============================================================


def diagnose_stitch_pairs():
  """
  Optional debugging only.


  Do not call this during normal capture because OpenCV 4.6.0 can crash
  with a FLANN error when a pair has too few feature matches.
  """


  image_paths, images = load_images_for_stitching()


  print("\n--- PAIR DIAGNOSIS ---")


  if len(images) < 2:
      print("Not enough images for pair diagnosis.")
      print("--- END DIAGNOSIS ---\n")
      return


  for i in range(len(images) - 1):
      pair = [images[i], images[i + 1]]


      name_a = os.path.basename(image_paths[i])
      name_b = os.path.basename(image_paths[i + 1])


      try:
          tester = cv2.Stitcher_create(cv2.Stitcher_PANORAMA)
          tester.setRegistrationResol(0.8)
          tester.setSeamEstimationResol(0.1)
          tester.setCompositingResol(-1)


          status, _ = tester.stitch(pair)


          if status == cv2.Stitcher_OK:
              result = "OK"
          else:
              result = f"FAIL code {status}"


      except cv2.error:
          result = "CRASHED, weak feature match"


      print(f"{name_a} <-> {name_b}: {result}")


  print("--- END DIAGNOSIS ---\n")


def diagnose_feature_matches():
  """
  Pair-by-pair ORB feature diagnosis for the saved panorama frames.


  This does not try to stitch. It only checks whether each neighboring
  image pair has enough shared visual features for OpenCV to match.


  Use this when stitching fails or only uses part of the sweep. The first
  BAD pair is usually where the panorama chain breaks.
  """


  if HEADLESS:
      print("Feature diagnosis skipped in HEADLESS mode.")
      return


  image_paths = sorted(
      glob.glob(os.path.join(UNSTITCHED_FOLDER, "*.jpg")),
      key=extract_capture_index
  )


  print("\n--- FEATURE MATCH DIAGNOSIS ---")
  print(f"Checking folder: {UNSTITCHED_FOLDER}")
  print(f"Found {len(image_paths)} saved frames.")


  if len(image_paths) < 2:
      print("Not enough images for feature diagnosis.")
      print("--- END FEATURE MATCH DIAGNOSIS ---\n")
      return


  orb = cv2.ORB_create(nfeatures=3000)
  matcher = cv2.BFMatcher(cv2.NORM_HAMMING, crossCheck=True)


  bad_pairs = []


  for i in range(len(image_paths) - 1):
      path_a = image_paths[i]
      path_b = image_paths[i + 1]
      name_a = os.path.basename(path_a)
      name_b = os.path.basename(path_b)


      img_a = cv2.imread(path_a, cv2.IMREAD_GRAYSCALE)
      img_b = cv2.imread(path_b, cv2.IMREAD_GRAYSCALE)


      if img_a is None or img_b is None:
          print(f"{name_a} -> {name_b}: could not read image BAD")
          bad_pairs.append((name_a, name_b, 0))
          continue


      img_a = imutils.resize(img_a, width=1000)
      img_b = imutils.resize(img_b, width=1000)


      kp_a, des_a = orb.detectAndCompute(img_a, None)
      kp_b, des_b = orb.detectAndCompute(img_b, None)


      if des_a is None or des_b is None:
          print(
              f"{name_a} -> {name_b}: 0 good matches, "
              f"kpA={len(kp_a)}, kpB={len(kp_b)} BAD"
          )
          bad_pairs.append((name_a, name_b, 0))
          continue


      matches = matcher.match(des_a, des_b)
      good_matches = [m for m in matches if m.distance < 55]
      score = len(good_matches)


      if score >= 80:
          status = "STRONG"
      elif score >= 40:
          status = "OK"
      elif score >= 20:
          status = "WEAK"
      else:
          status = "BAD"


      if score < 40:
          bad_pairs.append((name_a, name_b, score))


      print(
          f"{name_a} -> {name_b}: "
          f"{score} good matches, "
          f"kpA={len(kp_a)}, kpB={len(kp_b)} {status}"
      )


  if bad_pairs:
      print("\nLikely stitch breakpoints:")
      for name_a, name_b, score in bad_pairs:
          print(f"  {name_a} -> {name_b}: only {score} good matches")
      print("\nInterpretation: BAD/WEAK pairs usually mean not enough overlap, blur, bad exposure, or a blank/low-texture part of the room.")
  else:
      print("\nAll neighboring pairs had at least OK feature overlap.")
      print("If stitching still failed, the issue may be global geometry, parallax, exposure/color shifts, or OpenCV bundle adjustment.")


  print("--- END FEATURE MATCH DIAGNOSIS ---\n")


# ============================================================
# THERMAL PRINTING
# ============================================================


def make_thermal_print_image(img):
  """
  Converts a normal image into a thermal-printer-friendly image.


  Key idea:
  Thermal printers cannot print real gray.
  They fake gray using black dot density.
  So this function compresses whites, boosts detail, and dithers.
  """


  # Convert to grayscale.
  img = img.convert("L")


  # Rotate panorama for the printer.
  img = img.rotate(90, expand=True)


  # Resize to printer width.
  ratio = PRINTER_WIDTH / img.width
  height = int(img.height * ratio)
  img = img.resize((PRINTER_WIDTH, height), Image.LANCZOS)


  # Convert to NumPy for stronger processing.
  arr = np.array(img).astype(np.float32)


  # Normalize to 0 to 1.
  arr = arr / 255.0


  # Brighten midtones without completely nuking detail.
  arr = np.power(arr, PRINT_GAMMA)


  # Compress bright whites so they still print as very light gray.
  highlight_threshold = 0.78
  highlights = arr > highlight_threshold
  arr[highlights] = arr[highlights] - HIGHLIGHT_STRENGTH * (
      arr[highlights] - highlight_threshold
  )


  # Prevent pure white.
  # Pure white = no ink = same as receipt paper.
  arr = np.clip(arr, 0.0, WHITE_MAX_LEVEL)


  # Back to 8-bit grayscale.
  arr_8 = (arr * 255).astype(np.uint8)


  # Local contrast boost.
  clahe = cv2.createCLAHE(
      clipLimit=CLAHE_CLIP_LIMIT,
      tileGridSize=(8, 8)
  )
  arr_8 = clahe.apply(arr_8)


  # Sharpen details.
  blur = cv2.GaussianBlur(arr_8, (0, 0), 1.0)
  arr_8 = cv2.addWeighted(arr_8, 1.25, blur, -0.25, 0)


  # Add slight noise to very bright regions.
  # This makes white areas become faint printable dots instead of blank paper.
  arr_float = arr_8.astype(np.float32)
  bright_mask = arr_float > 205


  noise = np.random.normal(
      loc=0,
      scale=BRIGHT_NOISE_AMOUNT,
      size=arr_float.shape
  )


  arr_float[bright_mask] = arr_float[bright_mask] + noise[bright_mask]


  # Keep image light but not pure white.
  arr_float = np.clip(arr_float, 0, 235)
  arr_8 = arr_float.astype(np.uint8)


  final_img = Image.fromarray(arr_8).convert("L")


  # Floyd-Steinberg dithering turns gray into black dot patterns.
  final_img = final_img.convert("1", dither=Image.Dither.FLOYDSTEINBERG)


  return final_img


def make_logo_print_image(filepath):
  """
  Converts the logo into a thermal-printer-friendly image.


  ROTATE_LOGO_VERTICAL = False (default):
      The logo prints horizontally and perpendicular to the rotated panorama.
      LOGO_HORIZONTAL_WIDTH_SCALE controls how wide it is (0.5 = 50% of receipt width).
      The logo is centered on a white background.


  ROTATE_LOGO_VERTICAL = True:
      The logo is rotated to match the panorama orientation (parallel).
      LOGO_ROTATION_DEGREES and LOGO_VERTICAL_WIDTH_SCALE apply.


  LOGO_PADDING_PX white pixels are added above and below the logo in both cases,
  giving clearance so the logo does not get clipped at the cut line.
  """


  logo = Image.open(filepath).convert("L")


  if ROTATE_LOGO_VERTICAL:
      # Rotate to match the panorama (parallel orientation).
      logo = logo.rotate(LOGO_ROTATION_DEGREES, expand=True)


      target_width = int(PRINTER_WIDTH * LOGO_VERTICAL_WIDTH_SCALE)
      ratio = target_width / logo.width
      target_height = int(logo.height * ratio)
      logo = logo.resize((target_width, target_height), Image.LANCZOS)


      # Center on a white printer-width canvas with top/bottom padding.
      canvas_height = target_height + LOGO_PADDING_PX * 2
      canvas = Image.new("L", (PRINTER_WIDTH, canvas_height), 255)
      x_offset = (PRINTER_WIDTH - target_width) // 2
      canvas.paste(logo, (x_offset, LOGO_PADDING_PX))
      logo = canvas


  else:
      # Horizontal logo — perpendicular to the rotated panorama.
      # Scale to LOGO_HORIZONTAL_WIDTH_SCALE of the printer width.
      target_width = int(PRINTER_WIDTH * LOGO_HORIZONTAL_WIDTH_SCALE)
      ratio = target_width / logo.width
      target_height = int(logo.height * ratio)
      logo = logo.resize((target_width, target_height), Image.LANCZOS)


      # Center on a white printer-width canvas with top/bottom padding.
      canvas_height = target_height + LOGO_PADDING_PX * 2
      canvas = Image.new("L", (PRINTER_WIDTH, canvas_height), 255)
      x_offset = (PRINTER_WIDTH - target_width) // 2
      canvas.paste(logo, (x_offset, LOGO_PADDING_PX))
      logo = canvas


  # Make black parts darker and white background cleaner.
  logo = ImageEnhance.Contrast(logo).enhance(2.5)
  logo = ImageEnhance.Sharpness(logo).enhance(2.0)
  # Convert to black and white.
  # For logos, no dithering usually looks cleaner than dithering.
  logo = logo.convert("1", dither=Image.Dither.NONE)
  timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
  logo_temp_path = os.path.join(SAVE_DIR, f"logo_print_ready_{timestamp}.png")


  logo.save(logo_temp_path)


  return logo_temp_path


def process_and_print(filepath, qr_path=None):
  if HEADLESS:
      time.sleep(0.4)
      try:
          out = os.path.join(
              SAVE_DIR,
              f"panorama_{datetime.now().strftime('%Y%m%d_%H%M%S')}.png",
          )
          Image.open(filepath).save(out)
      except Exception:
          pass
      return


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


  final_img = make_thermal_print_image(img)
  timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
  temp_path = os.path.join(SAVE_DIR, f"panorama_print_ready_{timestamp}.png")


  final_img.save(temp_path)
  print(f"Print-ready panorama saved: {temp_path}")
  logo_temp_path = None


  if PRINT_LOGO_AT_END:
      if os.path.exists(LOGO_PATH):
          logo_temp_path = make_logo_print_image(LOGO_PATH)
          print(f"Print-ready logo saved: {logo_temp_path}")
      else:
          print(f"Logo file not found: {LOGO_PATH}")
          print("Skipping logo print.")


  try:
      p = Usb(0x0485, 0x5741)
      p.hw("INIT")
      if logo_temp_path:
          p.image(logo_temp_path)
      p.image(temp_path)
      if logo_temp_path:
          p.image(logo_temp_path)


      # Print QR code at bottom if user chose QR + Print option
      if qr_path and os.path.exists(qr_path):
          from duen_qr import _make_qr_print_image
          qr_ready = _make_qr_print_image(qr_path)
          p.image(qr_ready)


      p.cut()
      print("Panorama and logos printed successfully.")
  except Exception as e:
      print(f"Print failed: {e}")
      raise
  finally:
      try:
          p.device.reset()
          p.close()
      except Exception:
          pass
      time.sleep(2)


# ============================================================
# PERFECT STITCH CAPTURE FLOW WRAPPED FOR DUEN UI
# ============================================================


def capture_and_stitch_once(progress_callback=None, image_callback=None, stop_flag=None):
  """
  Run the exact proven Perfect Stitch capture path, but stop after stitching.


  progress_callback(event, **data) is optional and lets the UI update labels.
  image_callback(filepath, index, angle) is optional and lets the UI show each shot.
  """
  def emit(event, **data):
      if progress_callback:
          try:
              progress_callback(event, **data)
          except Exception:
              pass
  def should_stop():
      return stop_flag is not None and stop_flag()


  print("\n--- Starting DUEN Perfect Stitch capture ---")
  print(f"Capturing {len(ANGLES_TO_CAPTURE)} images...")


  setup_image_folders()
  if should_stop(): return None


  emit("warmup", message="Warming up camera exposure...")
  warmup_camera()
  if should_stop(): return None


  # Perfect Stitch first image fix:
  # The first move is the largest jump, so it gets a longer settle and a throwaway frame.
  first_angle = ANGLES_TO_CAPTURE[0]
  emit("move_first", angle=first_angle, message=f"Moving to first angle {first_angle}...")
  print(f"Moving to first angle: {first_angle}")
  move_servo(first_angle)
  if should_stop(): return None


  print(f"Waiting {FIRST_CAPTURE_WAIT} seconds for first angle to settle.")
  time.sleep(FIRST_CAPTURE_WAIT)
  if should_stop(): return None


  print("Flushing one throwaway frame before first real capture.")
  emit("flush_first", angle=first_angle, message="Flushing first frame...")
  flush_camera_once()
  if should_stop(): return None


  for index, angle in enumerate(ANGLES_TO_CAPTURE):
      if should_stop(): return None
      print(f"Capturing {index + 1}/{len(ANGLES_TO_CAPTURE)} at angle {angle}")
      emit("capture", index=index, angle=angle, total=len(ANGLES_TO_CAPTURE))


      if index != 0:
          move_servo(angle)
          time.sleep(FAST_SERVO_WAIT)
      if should_stop(): return None


      filepath = take_picture(angle, index)


      if image_callback:
          try:
              image_callback(filepath, index, angle)
          except Exception:
              pass


  print("Saving files.")
  subprocess.run(["sync"])


  move_servo(135)
  print("Servo returned to center.")


  emit("stitch", message="Stitching panorama...")
  success = stitch_images()


  if success and os.path.exists(STITCHED_OUTPUT):
      print("DUEN Perfect Stitch capture succeeded.")
      emit("done", output=STITCHED_OUTPUT)
      return STITCHED_OUTPUT


  print("Skipping print because stitching failed.")
  print("Check imageprinter/unstitchedImages/ for the raw photos.")


  print("Running feature-match diagnosis after failed stitch...")
  try:
      diagnose_feature_matches()
  except Exception as e:
      print(f"Feature-match diagnosis failed: {e}")


  emit("error", message="Stitching failed.")
  return None


def run_panorama_and_print_once():
  """
  Full one-shot Perfect Stitch behavior, kept inside DUEN hardware for testing.
  This captures, stitches, and prints immediately, just like the standalone script.
  The UI normally calls capture_and_stitch_once() first, then asks how many copies.
  """
  print("\n--- Starting one-shot panorama ---")
  print(f"Capturing {len(ANGLES_TO_CAPTURE)} images...")


  try:
      output = capture_and_stitch_once()
      if output and os.path.exists(output):
          process_and_print(output)
      else:
          print("Skipping print because stitching failed.")
  except Exception as e:
      print(f"Error during panorama capture: {e}")
  finally:
      print("--- One-shot run complete ---")


# ── LED RING CONTROLLER ───────────────────────────────────────
class LightController:
  def __init__(self, strip):
      self.strip      = strip
      self.mode       = DEFAULT_MODE
      self.brightness = LED_DEFAULT_BRIGHTNESS
      self._running   = True
      self._lock      = threading.Lock()
      # Flash sync: caller sets _flash_req, loop executes flash, sets _flash_done.
      self._flash_req  = threading.Event()
      self._flash_done = threading.Event()
      self._flash_frac = 1.0
      self._flash_dur  = 0.10
      self._t = threading.Thread(target=self._loop, daemon=True)
      self._t.start()


  def set_mode(self, mode):
      if mode not in LIGHT_MODES:
          return
      with self._lock:
          self.mode = mode


  def set_brightness(self, value):
      with self._lock:
          self.brightness = int(max(0, min(255, value)))
          self.strip.setBrightness(self.brightness)
  def flash_once(self, brightness_fraction=1.0, duration_s=0.10):
      """Request a single white flash. Blocks until the flash finishes (max 2 s)."""
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


  def _render_rainbow(self, j):
      n = self.strip.numPixels()
      for i in range(n):
          self.strip.setPixelColor(i, self._wheel((i * 256 // n + j) & 255))


  def _render_snake(self, head):
      n = self.strip.numPixels()
      for i in range(n):
          self.strip.setPixelColor(i, Color(0, 0, 0))
      for k in range(SNAKE_TAIL_LEN):
          idx     = (head - k) % n
          falloff = (SNAKE_TAIL_LEN - k) / SNAKE_TAIL_LEN
          self.strip.setPixelColor(
              idx, Color(int(255 * falloff), int(255 * falloff), int(255 * falloff))
          )


  def _loop(self):
      head = 0
      j    = 0
      while self._running:
          # Flash takes priority over the animation loop.
          if self._flash_req.is_set():
              self._do_flash()
              self._flash_req.clear()
              self._flash_done.set()
              continue
          with self._lock:
              mode = self.mode
          try:
              if mode in LIGHT_COLORS:
                  self._render_solid(LIGHT_COLORS[mode])
                  self.strip.show()
                  time.sleep(0.1)
              elif mode == "disco":
                  self._render_rainbow(j)
                  self.strip.show()
                  j = (j + 1) % 256
                  time.sleep(DISCO_STEP_MS / 1000.0)
              elif mode == "snake":
                  self._render_snake(head)
                  self.strip.show()
                  head = (head + 1) % self.strip.numPixels()
                  time.sleep(SNAKE_STEP_MS / 1000.0)
              else:
                  time.sleep(0.1)
          except Exception:
              time.sleep(0.1)


def cleanup_hardware():
  """Safely stop lights, servo pulses, camera, and pigpio when the UI quits."""
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





