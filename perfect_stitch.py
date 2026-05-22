# Perfect Stitch - Resize + First Image Fix Version


import pigpio
import time
import os
import shutil
import glob
import subprocess
import cv2
import numpy as np
import imutils
from picamera2 import Picamera2
from datetime import datetime
from escpos.printer import Usb
from PIL import Image, ImageEnhance, ExifTags




# ============================================================
# PIN CONFIGURATION
# ============================================================


SERVO_PIN = 26




# ============================================================
# FOLDER CONFIGURATION
# ============================================================


BASE_DIR = os.path.dirname(os.path.abspath(__file__))


SAVE_DIR = os.path.expanduser("~/photos")


IMAGE_FOLDER = os.path.join(BASE_DIR, "imageprinter")
UNSTITCHED_FOLDER = os.path.join(IMAGE_FOLDER, "unstitchedImages")
STITCHED_OUTPUT = os.path.join(IMAGE_FOLDER, "stitchedOutputProcessed.png")
RAW_STITCHED_OUTPUT = os.path.join(IMAGE_FOLDER, "stitchedOutputRaw.png")




# ============================================================
# LOGO CONFIGURATION
# ============================================================


LOGO_PATH = os.path.join(BASE_DIR, "duen_logo.png")


PRINT_LOGO_AT_END = True


ROTATE_LOGO_VERTICAL = False
LOGO_ROTATION_DEGREES = 270
LOGO_VERTICAL_WIDTH_SCALE = 0.55
LOGO_HORIZONTAL_WIDTH_SCALE = 0.50
LOGO_PADDING_PX = 40




# ============================================================
# SPEED + QUALITY SETTINGS
# ============================================================


# 1600 keeps more detail than 1200, but is still lighter than full 1920.
STITCH_RESIZE_WIDTH = 1600


# Normal wait between smaller servo movements.
FAST_SERVO_WAIT = 0.28


# The first movement is a big jump from warmup angle to first capture angle.
# Give the servo/chassis more time before the first real photo.
FIRST_CAPTURE_WAIT = 1.00


# Small delay after the throwaway frame before taking the real first photo.
FIRST_CAPTURE_FLUSH_WAIT = 0.20


POST_CAPTURE_WAIT = 0.20


IMAGE_WIDTH = 1920
IMAGE_HEIGHT = 1920


EXPOSURE_TIME_US = 8000
ANALOGUE_GAIN = 4.0




# ============================================================
# THERMAL PRINTER IMAGE SETTINGS
# ============================================================


PRINTER_WIDTH = 384


WHITE_MAX_LEVEL = 0.88
HIGHLIGHT_STRENGTH = 0.18
PRINT_GAMMA = 0.85
CLAHE_CLIP_LIMIT = 2.0
BRIGHT_NOISE_AMOUNT = 10




# ============================================================
# ANGLE CONFIGURATION
# ============================================================


ANGLES_TO_CAPTURE = [
    265, 247, 229, 211, 193, 175, 157, 139,
    121, 103, 88, 73, 58, 43, 28, 13
]


MIN_PULSE = 500
MAX_PULSE = 2500
MAX_DEGREES = 270




# ============================================================
# SETUP
# ============================================================


os.makedirs(SAVE_DIR, exist_ok=True)


pi = pigpio.pi()
if not pi.connected:
    raise RuntimeError("Could not connect to pigpiod. Run: sudo pigpiod")


camera = Picamera2()


config = camera.create_still_configuration(
    main={"size": (IMAGE_WIDTH, IMAGE_HEIGHT)}
)


camera.configure(config)


print("Starting camera...")
camera.start()


# Give the camera pipeline time to fully start.
time.sleep(4)


print("Camera ready.")
print(f"Will capture {len(ANGLES_TO_CAPTURE)} images once.")




# ============================================================
# SERVO HELPERS
# ============================================================


def angle_to_pulse(angle):
    angle = max(0, min(MAX_DEGREES, angle))
    pulse = MIN_PULSE + (angle / MAX_DEGREES) * (MAX_PULSE - MIN_PULSE)
    return int(pulse)




def move_servo(angle):
    if not pi.connected:
        raise RuntimeError("Lost connection to pigpiod during sweep!")


    pulse = angle_to_pulse(angle)
    pi.set_servo_pulsewidth(SERVO_PIN, pulse)




# ============================================================
# IMAGE CAPTURE
# ============================================================


def setup_image_folders():
    """
    Clears and recreates the image folders.


    This version uses absolute paths based on the Python file location.
    That prevents permission/path weirdness from running the script in
    different terminal folders.
    """


    print("Preparing image folders...")


    try:
        if os.path.exists(IMAGE_FOLDER):
            shutil.rmtree(IMAGE_FOLDER)


        os.makedirs(UNSTITCHED_FOLDER, exist_ok=True)


    except PermissionError:
        print("\nPermission error while preparing image folders.")
        print("Run this once in the same folder as your Python file:")
        print("sudo rm -rf imageprinter")
        print("sudo chown -R $USER:$USER .")
        print("chmod -R u+rwX .")
        raise


    print(f"Image folder ready: {UNSTITCHED_FOLDER}")




def safe_capture_metadata():
    try:
        return camera.capture_metadata()
    except Exception as e:
        print(f"Metadata read failed once: {e}")
        time.sleep(0.3)
        return camera.capture_metadata()




def warmup_camera():
    """
    Warm up exposure from the center of the sweep.
    This does not take a real picture.
    """


    print("Warming up camera...")


    move_servo(135)
    time.sleep(0.7)


    camera.set_controls({
        "AeEnable": True,
        "AwbEnable": True,
    })


    for _ in range(5):
        time.sleep(0.25)
        safe_capture_metadata()


    metadata = safe_capture_metadata()


    locked_exposure = metadata.get("ExposureTime", EXPOSURE_TIME_US)
    locked_gain = metadata.get("AnalogueGain", ANALOGUE_GAIN)


    print(f"Exposure locked: {locked_exposure} us, gain {locked_gain:.2f}")


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


    This helps avoid using a stale/unstable frame right after the servo moves.
    The throwaway image is deleted immediately.
    """


    temp_path = os.path.join(UNSTITCHED_FOLDER, "_throwaway_first_frame.jpg")


    try:
        print("Taking throwaway frame...")
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


    try:
        print(f"Saving photo to: {filename}")
        camera.capture_file(filename)
        time.sleep(POST_CAPTURE_WAIT)
        return filename


    except PermissionError:
        print("\nPermission denied while saving image.")
        print(f"Could not write to: {filename}")
        print("\nFix with:")
        print(f"sudo rm -rf {IMAGE_FOLDER}")
        print(f"sudo chown -R $USER:$USER {BASE_DIR}")
        print(f"chmod -R u+rwX {BASE_DIR}")
        raise


    except Exception as e:
        print(f"Capture failed at angle {angle}: {e}")
        time.sleep(0.5)


        print("Retrying capture...")
        camera.capture_file(filename)
        time.sleep(POST_CAPTURE_WAIT)
        return filename




# ============================================================
# PANORAMA STITCHING
# ============================================================


def extract_capture_index(path):
    filename = os.path.basename(path)
    return int(filename.split("_")[0])




def crop_only_outer_black(stitched_img):
    gray = cv2.cvtColor(stitched_img, cv2.COLOR_BGR2GRAY)


    mask = (gray > 10).astype(np.uint8)


    if mask.sum() == 0:
        print("No visible pixels found. Saving raw stitched image.")
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


    images = []


    print("\nImages being loaded for stitching:")
    print(f"Resize width for stitching: {STITCH_RESIZE_WIDTH}")


    for image_path in image_paths:
        print("\nLoading:", os.path.basename(image_path))


        img = cv2.imread(image_path)


        if img is None:
            print("Could not read:", image_path)
            continue


        original_height, original_width = img.shape[:2]
        print(f"Original size: {original_width}x{original_height}")


        img_stitch = imutils.resize(img, width=STITCH_RESIZE_WIDTH)


        stitch_height, stitch_width = img_stitch.shape[:2]
        print(f"Stitch size:   {stitch_width}x{stitch_height}")


        images.append(img_stitch)


    return image_paths, images




def stitch_images():
    image_paths, images = load_images_for_stitching()


    print(f"\nStitching {len(images)} images...")


    if len(images) < 2:
        raise ValueError("Need at least 2 images to stitch.")


    imageStitcher = cv2.Stitcher_create(cv2.Stitcher_PANORAMA)


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


    elif status == cv2.Stitcher_ERR_HOMOGRAPHY_EST_FAIL:
        print("Reason: Images could not align.")
        print("Try better lighting, more overlap, or more servo settle time.")


    elif status == cv2.Stitcher_ERR_CAMERA_PARAMS_ADJUST_FAIL:
        print("Reason: Camera parameter adjustment failed.")
        print("Most likely cause: weak matches or unstable stitch geometry.")


    return False




# ============================================================
# THERMAL PRINTING
# ============================================================


def make_thermal_print_image(img):
    img = img.convert("L")


    img = img.rotate(90, expand=True)


    ratio = PRINTER_WIDTH / img.width
    height = int(img.height * ratio)
    img = img.resize((PRINTER_WIDTH, height), Image.LANCZOS)


    arr = np.array(img).astype(np.float32)
    arr = arr / 255.0


    arr = np.power(arr, PRINT_GAMMA)


    highlight_threshold = 0.78
    highlights = arr > highlight_threshold
    arr[highlights] = arr[highlights] - HIGHLIGHT_STRENGTH * (
        arr[highlights] - highlight_threshold
    )


    arr = np.clip(arr, 0.0, WHITE_MAX_LEVEL)


    arr_8 = (arr * 255).astype(np.uint8)


    clahe = cv2.createCLAHE(
        clipLimit=CLAHE_CLIP_LIMIT,
        tileGridSize=(8, 8)
    )
    arr_8 = clahe.apply(arr_8)


    blur = cv2.GaussianBlur(arr_8, (0, 0), 1.0)
    arr_8 = cv2.addWeighted(arr_8, 1.45, blur, -0.45, 0)


    arr_float = arr_8.astype(np.float32)
    bright_mask = arr_float > 205


    noise = np.random.normal(
        loc=0,
        scale=BRIGHT_NOISE_AMOUNT,
        size=arr_float.shape
    )


    arr_float[bright_mask] = arr_float[bright_mask] + noise[bright_mask]


    arr_float = np.clip(arr_float, 0, 235)
    arr_8 = arr_float.astype(np.uint8)


    final_img = Image.fromarray(arr_8).convert("L")
    final_img = final_img.convert("1", dither=Image.Dither.FLOYDSTEINBERG)


    return final_img




def make_logo_print_image(filepath):
    logo = Image.open(filepath).convert("L")


    if ROTATE_LOGO_VERTICAL:
        logo = logo.rotate(LOGO_ROTATION_DEGREES, expand=True)


        target_width = int(PRINTER_WIDTH * LOGO_VERTICAL_WIDTH_SCALE)
        ratio = target_width / logo.width
        target_height = int(logo.height * ratio)
        logo = logo.resize((target_width, target_height), Image.LANCZOS)


        canvas_height = target_height + LOGO_PADDING_PX * 2
        canvas = Image.new("L", (PRINTER_WIDTH, canvas_height), 255)
        x_offset = (PRINTER_WIDTH - target_width) // 2
        canvas.paste(logo, (x_offset, LOGO_PADDING_PX))
        logo = canvas


    else:
        target_width = int(PRINTER_WIDTH * LOGO_HORIZONTAL_WIDTH_SCALE)
        ratio = target_width / logo.width
        target_height = int(logo.height * ratio)
        logo = logo.resize((target_width, target_height), Image.LANCZOS)


        canvas_height = target_height + LOGO_PADDING_PX * 2
        canvas = Image.new("L", (PRINTER_WIDTH, canvas_height), 255)
        x_offset = (PRINTER_WIDTH - target_width) // 2
        canvas.paste(logo, (x_offset, LOGO_PADDING_PX))
        logo = canvas


    logo = ImageEnhance.Contrast(logo).enhance(2.5)
    logo = ImageEnhance.Sharpness(logo).enhance(2.0)


    logo = logo.convert("1", dither=Image.Dither.NONE)


    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    logo_temp_path = os.path.join(SAVE_DIR, f"logo_print_ready_{timestamp}.png")


    logo.save(logo_temp_path)


    return logo_temp_path




def process_and_print(filepath):
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


        p.cut()


        print("Panorama and logos printed successfully.")


    except Exception as e:
        print(f"Print failed: {e}")




# ============================================================
# MAIN ONE-SHOT FLOW
# ============================================================


def run_panorama_and_print_once():
    print("\n--- Starting one-shot panorama ---")
    print(f"Capturing {len(ANGLES_TO_CAPTURE)} images...")


    try:
        setup_image_folders()


        warmup_camera()


        # Move to the first angle before taking the first real picture.
        # This first move is large, so we wait longer and flush one frame.
        first_angle = ANGLES_TO_CAPTURE[0]
        print(f"Moving to first angle: {first_angle}")
        move_servo(first_angle)


        print(f"Waiting {FIRST_CAPTURE_WAIT} seconds for first angle to settle...")
        time.sleep(FIRST_CAPTURE_WAIT)


        print("Flushing one throwaway frame before first real capture...")
        flush_camera_once()


        for index, angle in enumerate(ANGLES_TO_CAPTURE):
            print(f"Capturing {index + 1}/{len(ANGLES_TO_CAPTURE)} at angle {angle}")


            if index != 0:
                move_servo(angle)
                time.sleep(FAST_SERVO_WAIT)


            take_picture(angle, index)


        print("Saving files...")
        subprocess.run(["sync"])


        move_servo(135)
        print("Servo returned to center.")


        success = stitch_images()


        if success and os.path.exists(STITCHED_OUTPUT):
            process_and_print(STITCHED_OUTPUT)
        else:
            print("Skipping print because stitching failed.")
            print("Check imageprinter/unstitchedImages/ for the raw photos.")


    except Exception as e:
        print(f"Error during panorama capture: {e}")


    finally:
        print("--- One-shot run complete ---")




# ============================================================
# START IMMEDIATELY, RUN ONCE, THEN EXIT
# ============================================================


try:
    run_panorama_and_print_once()


except KeyboardInterrupt:
    print("\nStopped by user.")


finally:
    print("Cleaning up camera and servo.")


    try:
        camera.stop()
    except Exception:
        pass


    try:
        pi.set_servo_pulsewidth(SERVO_PIN, 0)
        pi.stop()
    except Exception:
        pass


    print("Shutdown complete.")



