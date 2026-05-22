"""
================================================================================
DUEN PROJECT - QR Code & Google Drive Upload
================================================================================
FILE PURPOSE:
    This file handles uploading panorama photos to Google Drive and generating
    QR codes so users can scan and view their photo on their phone instantly.

    This file is STANDALONE — it does not modify duen_ui.py or duen_hardware.py.
    It is designed to be called from buttons that will be added to duen_ui.py later.

================================================================================
PROJECT CONTEXT (read this first):
================================================================================

    This is the DUEN PROJECT — a Raspberry Pi photobooth system.
    The photobooth takes panoramic photos using a servo-swept camera,
    stitches them together, and prints them on receipt paper strips.

    This file is one of several modules:
        duen_ui.py       — Tkinter touchscreen UI (800x480)
        duen_hardware.py — All hardware logic: servo, camera, stitching, printing, LEDs
        duen_qr.py       — THIS FILE: Google Drive upload + QR code generation

    The UI imports duen_hardware as hw. The print function is:
        hw.process_and_print(hw.STITCHED_OUTPUT)

    The stitched panorama is always saved to:
        imageprinter/stitchedOutputProcessed.png  (hw.STITCHED_OUTPUT)

    Photos and logs are saved to:
        ~/photos/

    The thermal printer is USB:
        Vendor ID:  0x0485
        Product ID: 0x5741
        Width:      384px (receipt paper)

    Print strip layout (top to bottom on receipt paper):
        Logo  →  Panorama  →  Logo

    When QR is added, the layout will become:
        Logo  →  Panorama  →  QR Code

================================================================================
WHAT THIS FILE DOES:
================================================================================

    1. Uploads the stitched panorama to a Google Drive folder
       called "Photobooth Photos"
    2. Sets the file sharing to "anyone with link can view"
    3. Builds a share URL: https://drive.google.com/file/d/{file_id}/view
    4. Generates a QR code image from that URL
    5. Optionally prints the strip with QR code at the bottom

================================================================================
FUNCTIONS IN THIS FILE:
================================================================================

    upload_and_generate_qr(image_path)
        The main function. Call this when user taps the QR button.
        Uploads to Drive, generates QR, returns a dict:
            {
                'qr_path': '/home/photobooth/photos/qr_codes/qr_TIMESTAMP.png',
                'url':     'https://drive.google.com/file/d/.../view',
                'error':   None   (or an error string if something failed)
            }
        Example:
            import duen_qr
            result = duen_qr.upload_and_generate_qr(hw.STITCHED_OUTPUT)
            if result['qr_path']:
                # show QR on screen or print it

    generate_qr_only(url)
        Generates a QR code image from any URL without uploading anything.
        Returns the path to the saved QR image, or None on failure.
        Use this if you already have a Drive URL and just need the image.

    print_with_qr(image_path, qr_path)
        Prints the full receipt strip: Logo → Panorama → QR Code
        NOTE: This function will be removed later when duen_hardware.py is
        updated to accept an optional qr_path parameter in process_and_print().
        For now it duplicates some print logic from duen_hardware.py.

================================================================================
HOW THE THREE PRINT BUTTONS WILL WORK (to be added to duen_ui.py later):
================================================================================

    # Button 1 — QR + Print
    # Uploads photo, generates QR, prints strip with QR at bottom,
    # and shows QR on screen simultaneously.
    result = duen_qr.upload_and_generate_qr(hw.STITCHED_OUTPUT)
    if result['qr_path']:
        duen_qr.print_with_qr(hw.STITCHED_OUTPUT, result['qr_path'])
        # also display result['qr_path'] image on screen

    # Button 2 — QR screen only (no QR on the printed strip)
    # Uploads photo and shows QR on screen, but prints normally without QR.
    result = duen_qr.upload_and_generate_qr(hw.STITCHED_OUTPUT)
    if result['qr_path']:
        hw.process_and_print(hw.STITCHED_OUTPUT)
        # also display result['qr_path'] image on screen

    # Button 3 — Print only (no QR anywhere)
    # Prints exactly as it did before this file existed.
    hw.process_and_print(hw.STITCHED_OUTPUT)

================================================================================
PLANNED FUTURE CHANGES (do not implement yet, owner will decide when):
================================================================================

    1. duen_hardware.py — modify process_and_print() to accept optional qr_path:
           def process_and_print(filepath, qr_path=None):
       This removes the need for print_with_qr() in this file.

    2. duen_ui.py — add three buttons to the print picker overlay replacing
       the current single PRINT button.

    3. duen_ui.py — add a QR display screen or overlay so the user can
       see and scan the QR code on the touchscreen.

    4. duen_ui.py — add uploading status indicator so user knows the
       upload is happening in the background.

    5. This file — remove print_with_qr() once duen_hardware.py is updated.

================================================================================
SETUP — one-time installation (do this before running):
================================================================================

    ⚠ IMPORTANT — BEFORE YOU START:
        The DUEN Gmail login credentials (email address and password) are
        stored in the shared Google Drive folder for this project.
        Check the Google Drive folder your teammate shared with you and
        look for a document or note with the Gmail login details before
        attempting any of the steps below. You will need the Gmail address
        and password to sign in at Step 2 and Step 6.

    Step 1 — Install required libraries:
        pip install qrcode[pil] google-api-python-client \
            google-auth-httplib2 google-auth-oauthlib --break-system-packages

    Step 2 — Create a Google Cloud project:
        Go to: https://console.cloud.google.com/
        Sign in with the DUEN Gmail account (credentials are in Google Drive — see above)
        Click project dropdown → New Project → name it "Photobooth" → Create

    Step 3 — Enable the Google Drive API:
        Search bar → "Google Drive API" → click it → click Enable

    Step 4 — Configure OAuth consent screen:
        Left menu → APIs & Services → OAuth consent screen
        Select External → Create
        App name: Photobooth
        User support email: DUEN Gmail
        Developer contact: DUEN Gmail
        Save and Continue (don't add scopes)
        Test Users → Add Users → add DUEN Gmail → Save and Continue
        Back to Dashboard

    Step 5 — Create OAuth credentials:
        Left menu → APIs & Services → Credentials
        + Create Credentials → OAuth client ID
        Application type: Desktop app
        Name: Photobooth Pi
        Create → Download JSON
        Rename the downloaded file to exactly: credentials.json
        Copy credentials.json into the same folder as this file:
            /home/photobooth/duen/credentials.json

    Step 6 — First-time authorization (only needed once):
        Run this file directly:
            python3 duen_qr.py
        A browser window will open on the Pi.
        Sign in with the DUEN Gmail.
        You will see "Google hasn't verified this app" — this is normal.
        Click Advanced → Go to Photobooth (unsafe) → Allow
        Browser shows "The authentication flow has completed"
        A token.json file is saved automatically.
        All future uploads happen silently without a browser.

    Step 7 — Verify it works:
        The test mode (at the bottom of this file) will upload the most
        recent panorama and print the QR code path and URL.
        Open the QR image and scan it with a phone:
            xdg-open /home/photobooth/photos/qr_codes/qr_*.png

================================================================================
HOW TO TEST THIS FILE INDEPENDENTLY:
================================================================================

    Make sure a panorama has been captured first, then run:
        python3 duen_qr.py

    Success output looks like:
        [test] ✓ Success!
        [test] QR Code : /home/photobooth/photos/qr_codes/qr_TIMESTAMP.png
        [test] URL     : https://drive.google.com/file/d/.../view?usp=sharing

    Common errors and fixes:
        "credentials.json not found"
            → Put credentials.json in /home/photobooth/duen/

        "Authorization failed"
            → Make sure DUEN Gmail was added as a test user in Step 4

        "Upload error" or "No internet"
            → Test connection: ping google.com

        "No photos found"
            → Run a panorama first, or copy any jpg into ~/photos/

================================================================================
FILE LOCATIONS THIS FILE READS/WRITES:
================================================================================

    Reads:
        /home/photobooth/duen/credentials.json   OAuth credentials (you provide)
        /home/photobooth/duen/token.json         Auth token (auto-generated)
        /home/photobooth/duen/duen_logo.png      Logo for printing
        imageprinter/stitchedOutputProcessed.png Panorama to upload

    Writes:
        /home/photobooth/photos/qr_codes/        QR code images
        /home/photobooth/photos/qr_log.txt       Upload log
        /home/photobooth/photos/qr_print_ready_* Printer-ready QR images (temp)

================================================================================
"""

import os
import glob
import time
from datetime import datetime
from PIL import Image, ImageEnhance

# ─── CONFIG ────────────────────────────────────────────────────────────────────
# Only change these if your setup is different.

DRIVE_FOLDER_NAME  = "Photobooth Photos"
                                # Name of the Google Drive folder.
                                # Created automatically on first upload.

BASE_DIR           = os.path.dirname(os.path.abspath(__file__))
CREDENTIALS_FILE   = os.path.join(BASE_DIR, "credentials.json")
TOKEN_FILE         = os.path.join(BASE_DIR, "token.json")
SAVE_DIR           = os.path.expanduser("~/photos")
LOGO_PATH          = os.path.join(BASE_DIR, "duen_logo.png")
QR_OUTPUT_DIR      = os.path.join(SAVE_DIR, "qr_codes")
LOG_FILE           = os.path.join(SAVE_DIR, "qr_log.txt")

PRINTER_VENDOR_ID  = 0x0485     # Must match duen_hardware.py
PRINTER_PRODUCT_ID = 0x5741     # Must match duen_hardware.py
PRINTER_WIDTH      = 384        # Must match duen_hardware.py

QR_SIZE_PIXELS     = 384        # Matches printer width so QR fills full receipt
QR_BORDER          = 4          # Standard QR border size, do not change
QR_FILL_COLOR      = "black"
QR_BACK_COLOR      = "white"
LOGO_PADDING_PX    = 40         # Must match duen_hardware.py

SCOPES             = ["https://www.googleapis.com/auth/drive.file"]
                                # drive.file = only access files this app creates.
                                # Safer than full Drive access. Do not change.

os.makedirs(SAVE_DIR, exist_ok=True)
os.makedirs(QR_OUTPUT_DIR, exist_ok=True)


# ─── LOGGING ───────────────────────────────────────────────────────────────────

def _log(message, success=True, error=None):
    """Writes upload/QR attempt to log file with timestamp."""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    status    = "SUCCESS" if success else "FAILED"
    line      = f"[{timestamp}] {status} — {message}"
    if error:
        line += f" — error: {error}"
    line += "\n"
    try:
        with open(LOG_FILE, "a") as f:
            f.write(line)
    except Exception as e:
        print(f"[qr] Warning: Could not write to log: {e}")


# ─── GOOGLE DRIVE AUTH ─────────────────────────────────────────────────────────

def _get_drive_service():
    """
    Authenticates with Google Drive and returns a service object.
    First run opens browser for login and saves token.json.
    All subsequent runs use token.json silently without a browser.
    Returns service object, or None on failure.
    """
    try:
        from google.oauth2.credentials import Credentials
        from google_auth_oauthlib.flow import InstalledAppFlow
        from google.auth.transport.requests import Request
        from googleapiclient.discovery import build
    except ImportError:
        print("[qr] ERROR: Google API libraries not installed.")
        print("     Run: pip install google-api-python-client google-auth-httplib2 google-auth-oauthlib")
        return None

    creds = None

    if os.path.exists(TOKEN_FILE):
        try:
            creds = Credentials.from_authorized_user_file(TOKEN_FILE, SCOPES)
        except Exception as e:
            print(f"[qr] Could not load token: {e}")
            creds = None

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            try:
                from google.auth.transport.requests import Request
                creds.refresh(Request())
                print("[qr] Token refreshed.")
            except Exception as e:
                print(f"[qr] Token refresh failed: {e}")
                creds = None

        if not creds:
            if not os.path.exists(CREDENTIALS_FILE):
                print(f"[qr] ERROR: {CREDENTIALS_FILE} not found.")
                print("[qr] See setup instructions at the top of this file.")
                return None

            print("[qr] First-time login — opening browser...")
            try:
                flow  = InstalledAppFlow.from_client_secrets_file(CREDENTIALS_FILE, SCOPES)
                creds = flow.run_local_server(port=0)
                print("[qr] Authorization successful!")
            except Exception as e:
                print(f"[qr] Authorization failed: {e}")
                return None

        try:
            with open(TOKEN_FILE, "w") as token:
                token.write(creds.to_json())
        except Exception as e:
            print(f"[qr] Could not save token: {e}")

    try:
        from googleapiclient.discovery import build
        return build("drive", "v3", credentials=creds)
    except Exception as e:
        print(f"[qr] Failed to build Drive service: {e}")
        return None


# ─── DRIVE FOLDER ──────────────────────────────────────────────────────────────

def _get_or_create_folder(service):
    """
    Finds the Photobooth Photos folder in Drive, or creates it if missing.
    Returns the folder ID string, or None on failure.
    """
    try:
        query  = (
            f"name='{DRIVE_FOLDER_NAME}' "
            f"and mimeType='application/vnd.google-apps.folder' "
            f"and trashed=false"
        )
        result = service.files().list(q=query, fields="files(id, name)").execute()
        items  = result.get("files", [])
        if items:
            return items[0]["id"]

        print(f"[qr] Creating Drive folder: {DRIVE_FOLDER_NAME}")
        folder = service.files().create(
            body={"name": DRIVE_FOLDER_NAME,
                  "mimeType": "application/vnd.google-apps.folder"},
            fields="id"
        ).execute()
        return folder.get("id")
    except Exception as e:
        print(f"[qr] Folder error: {e}")
        return None


# ─── DRIVE UPLOAD ──────────────────────────────────────────────────────────────

def _upload_to_drive(service, image_path, folder_id):
    """
    Uploads image to Drive folder.
    Sets sharing to anyone with link can view.
    Returns the shareable URL string, or None on failure.
    """
    try:
        from googleapiclient.http import MediaFileUpload

        filename = os.path.basename(image_path)
        print(f"[qr] Uploading {filename}...")

        media    = MediaFileUpload(image_path, mimetype="image/jpeg", resumable=True)
        uploaded = service.files().create(
            body={"name": filename, "parents": [folder_id]},
            media_body=media,
            fields="id"
        ).execute()

        file_id = uploaded.get("id")
        print(f"[qr] Uploaded. File ID: {file_id}")

        service.permissions().create(
            fileId=file_id,
            body={"type": "anyone", "role": "reader"}
        ).execute()
        print("[qr] Sharing set: anyone with link can view.")

        share_url = f"https://drive.google.com/file/d/{file_id}/view?usp=sharing"
        return share_url

    except Exception as e:
        print(f"[qr] Upload error: {e}")
        return None


# ─── QR CODE GENERATION ────────────────────────────────────────────────────────

def generate_qr_only(url):
    """
    Generates a QR code image from any URL without uploading anything.
    Returns the path to the saved QR image, or None on failure.
    Call this directly if you already have a Drive URL.
    """
    try:
        import qrcode

        print(f"[qr] Generating QR code...")

        approx_modules = 33
        box_size = max(1, QR_SIZE_PIXELS // (approx_modules + 2 * QR_BORDER))

        qr = qrcode.QRCode(
            version=None,
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=box_size,
            border=QR_BORDER,
        )
        qr.add_data(url)
        qr.make(fit=True)

        img = qr.make_image(fill_color=QR_FILL_COLOR, back_color=QR_BACK_COLOR)
        img = img.resize((QR_SIZE_PIXELS, QR_SIZE_PIXELS), Image.NEAREST)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        qr_path   = os.path.join(QR_OUTPUT_DIR, f"qr_{timestamp}.png")
        img.save(qr_path)
        print(f"[qr] QR code saved: {qr_path}")
        return qr_path

    except ImportError:
        print("[qr] ERROR: qrcode library not installed.")
        print("     Run: pip install qrcode[pil]")
        return None
    except Exception as e:
        print(f"[qr] QR generation error: {e}")
        return None


# ─── MAIN PUBLIC FUNCTION ──────────────────────────────────────────────────────

def upload_and_generate_qr(image_path):
    """
    Full pipeline: upload photo to Drive → set sharing → generate QR code.

    Parameters:
        image_path → full path to the photo file
                     use hw.STITCHED_OUTPUT from duen_hardware.py

    Returns dict with keys:
        'qr_path' → path to QR code image file, or None on failure
        'url'     → Google Drive share URL, or None on failure
        'error'   → error message string, or None on success

    Example:
        import duen_qr
        import duen_hardware as hw
        result = duen_qr.upload_and_generate_qr(hw.STITCHED_OUTPUT)
        if result['qr_path']:
            print(f"QR saved to: {result['qr_path']}")
            print(f"URL: {result['url']}")
        else:
            print(f"Failed: {result['error']}")
    """
    result = {"qr_path": None, "url": None, "error": None}

    if not os.path.exists(image_path):
        result["error"] = f"Image not found: {image_path}"
        _log(f"upload skipped — file not found: {image_path}", success=False)
        return result

    print(f"\n[qr] Starting upload for: {image_path}")

    service = _get_drive_service()
    if not service:
        result["error"] = "Google Drive auth failed. Check credentials.json."
        _log("auth failed", success=False, error=result["error"])
        return result

    folder_id = _get_or_create_folder(service)
    if not folder_id:
        result["error"] = "Could not access Drive folder."
        _log("folder error", success=False, error=result["error"])
        return result

    share_url = _upload_to_drive(service, image_path, folder_id)
    if not share_url:
        result["error"] = "Upload to Drive failed. Check internet connection."
        _log(f"upload failed: {image_path}", success=False, error=result["error"])
        return result

    qr_path = generate_qr_only(share_url)
    if not qr_path:
        result["error"] = "QR generation failed (upload succeeded)."
        result["url"]   = share_url
        _log(f"qr gen failed but uploaded: {share_url}", success=False)
        return result

    result["qr_path"] = qr_path
    result["url"]     = share_url
    _log(f"uploaded {os.path.basename(image_path)} → {share_url}", success=True)
    print(f"[qr] Done. URL: {share_url}")
    return result


# ─── PRINT WITH QR ─────────────────────────────────────────────────────────────
# NOTE: This function will be removed later when duen_hardware.py is updated
# to accept an optional qr_path parameter in process_and_print().
# Do not add new logic here — keep it minimal.

def _make_qr_print_image(qr_path):
    """
    Converts QR code into a printer-ready 1-bit image with padding.
    Matches the logo formatting style from duen_hardware.py.
    Internal helper — not called directly from outside this file.
    """
    qr     = Image.open(qr_path).convert("L")
    ratio  = PRINTER_WIDTH / qr.width
    height = int(qr.height * ratio)
    qr     = qr.resize((PRINTER_WIDTH, height), Image.LANCZOS)

    canvas_h = height + LOGO_PADDING_PX * 2
    canvas   = Image.new("L", (PRINTER_WIDTH, canvas_h), 255)
    canvas.paste(qr, (0, LOGO_PADDING_PX))

    canvas = ImageEnhance.Contrast(canvas).enhance(2.5)
    canvas = ImageEnhance.Sharpness(canvas).enhance(2.0)
    canvas = canvas.convert("1", dither=Image.Dither.NONE)

    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path  = os.path.join(SAVE_DIR, f"qr_print_ready_{timestamp}.png")
    canvas.save(out_path)
    return out_path


def print_with_qr(image_path, qr_path):
    """
    Prints the full receipt strip with QR code at the bottom.

    Strip layout top to bottom:
        Logo  →  Panorama  →  QR Code

    Parameters:
        image_path → path to stitched panorama (use hw.STITCHED_OUTPUT)
        qr_path    → path to QR image (returned by upload_and_generate_qr())

    Returns True on success, False on failure.

    NOTE: This function will be removed later when duen_hardware.py is
    updated. Do not build new features into this function.
    """
    try:
        import duen_hardware as hw
        from escpos.printer import Usb

        print("[qr] Starting print with QR...")

        img       = Image.open(image_path)
        final_img = hw.make_thermal_print_image(img)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        pano_path = os.path.join(SAVE_DIR, f"panorama_print_ready_{timestamp}.png")
        final_img.save(pano_path)

        logo_path = None
        if os.path.exists(hw.LOGO_PATH):
            logo_path = hw.make_logo_print_image(hw.LOGO_PATH)

        qr_ready_path = _make_qr_print_image(qr_path)

        p = Usb(PRINTER_VENDOR_ID, PRINTER_PRODUCT_ID)
        p.hw("INIT")

        if logo_path:
            p.image(logo_path)      # Logo at top
        p.image(pano_path)          # Panorama in middle
        p.image(qr_ready_path)      # QR code at bottom
        p.cut()

        print("[qr] Printed: logo → panorama → QR code")
        return True

    except ImportError as e:
        print(f"[qr] Import error: {e}")
        return False
    except Exception as e:
        print(f"[qr] Print failed: {e}")
        return False


# ─── STANDALONE TEST ───────────────────────────────────────────────────────────

if __name__ == "__main__":
    print("=" * 60)
    print("  DUEN QR Code — Standalone Test Mode")
    print("=" * 60 + "\n")

    if not os.path.exists(CREDENTIALS_FILE):
        print(f"[test] credentials.json not found at {CREDENTIALS_FILE}")
        print("[test] See setup instructions at the top of this file.")
        exit(1)

    # Check stitched output first, then fall back to any photo in ~/photos/
    stitched = os.path.join(BASE_DIR, "imageprinter", "stitchedOutputProcessed.png")
    photos   = sorted(
        glob.glob(os.path.join(SAVE_DIR, "*.jpg")) +
        glob.glob(os.path.join(SAVE_DIR, "*.png")),
        key=os.path.getmtime, reverse=True
    )
    if os.path.exists(stitched):
        photos.insert(0, stitched)

    if not photos:
        print(f"[test] No photos found.")
        print("[test] Run a panorama capture first, or drop any image into ~/photos/")
        exit(1)

    latest = photos[0]
    print(f"[test] Using photo: {latest}\n")

    result = upload_and_generate_qr(latest)

    if result["qr_path"]:
        print(f"\n[test] ✓ Success!")
        print(f"[test] QR Code : {result['qr_path']}")
        print(f"[test] URL     : {result['url']}")
        print(f"\n[test] Scan the QR code with your phone:")
        print(f"       xdg-open {result['qr_path']}")
    else:
        print(f"\n[test] ✗ Failed: {result['error']}")
