# photo_filters.py — Print filters for the DUEN Panorama Booth
#
# Drop alongside booth_core.py and booth_v2_ui.py.
# Dependencies: Pillow (always) + OpenCV/NumPy (Pi hardware, optional).
# Every filter degrades gracefully to a Pillow-only fallback when cv2 is absent.
#
# ── Public API ────────────────────────────────────────────────────────────────
#
#   FILTERS                              dict  key → human label
#   apply_filter(pil_img, key, **kw)  →  PIL.Image   master dispatcher
#
#   apply_branding_overlay(pil_img)   →  PIL.Image
#   apply_comic_filter(pil_img)       →  PIL.Image
#   apply_pixel_filter(pil_img)       →  PIL.Image
#   apply_cctv_filter(pil_img)        →  PIL.Image
#
# ── Filter summaries ──────────────────────────────────────────────────────────
#
#   BRANDING OVERLAY
#     Full-width DUEN header bar prepended above the panorama.
#     Contains: "DUEN" logotype · event subtitle · right-aligned timestamp.
#     Brand colours (deep violet / accent purple) survive thermal dithering.
#
#   COMIC / CARTOON
#     1. Bilateral smoothing  — flattens skin texture, keeps contours sharp
#     2. Colour quantisation  — cel-shaded palette
#     3. Adaptive + Canny edges — ink-style outlines
#     4. Edge dilation        — chunky hand-drawn look
#     5. Edge compositing     — outlines burnt onto flat colour
#     6. Saturation boost     — punchy comic-panel colours
#
#   PIXEL / RETRO
#     1. Downscale to block grid (pixelate)
#     2. Colour quantise — limited 8-bit palette
#     3. Upscale NEAREST — hard block edges
#     4. Scanline overlay — CRT horizontal rules
#     5. Radial vignette  — corner darkening
#     6. Contrast + saturation boost
#
#   CCTV / SECURITY CAMERA
#     1. Desaturate to green-tinted greyscale (night-vision phosphor glow)
#     2. Gaussian noise — analogue tape grain
#     3. Horizontal scanlines — interlaced CRT artefact
#     4. Radial vignette — lens fall-off
#     5. Timestamp overlay — bottom-left, monospaced, amber-green
#     6. Camera ID + location overlay — top-left
#     7. REC icon — red dot + "REC" label, top-right


import os
import math
import random
from datetime import datetime


# ── Optional cv2 / numpy ──────────────────────────────────────────────────────
try:
    import cv2
    import numpy as np
    _HAVE_CV2 = True
except ImportError:
    _HAVE_CV2 = False
    cv2 = None
    np  = None


from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont


# ── Filter registry ───────────────────────────────────────────────────────────
FILTERS = {
    "none":     "None",
    "branding": "Branding",
    "comic":    "Comic",
    "pixel":    "Pixel / Retro",
    "sketch":   "Pencil Sketch",
}


# =============================================================================
# TUNABLE PARAMETERS
# Edit values here; no other file needs changing.
# =============================================================================


# ── Branding overlay ──────────────────────────────────────────────────────────
BRANDING_PARAMS = {
    "title":              "DUEN",
    "event_subtitle":     "PANORAMA EVENT",
    # Bigger footer because this is printed on a 384px thermal printer.
    # The old 10% footer made the event title tiny after resizing for print.
    "header_height_frac": 0.18,
    "header_min_px":      95,
    "header_max_px":      260,
    "bg_color":           (18,  12,  50),    # deep violet
    "accent_color":       (124, 106, 247),   # #7c6af7
    "title_color":        (255, 255, 255),
    "subtitle_color":     (165, 151, 255),   # #a597ff
    "timestamp_color":    (90,  90,  120),
    "rule_height":        4,
    "timestamp_fmt":      "%a %d %b %Y  .  %H:%M",
    "duen_font_frac":     0.22,
    "event_font_frac":    0.42,
    "timestamp_font_frac": 0.14,
    "font_paths": [
        "/usr/share/fonts/truetype/dejavu/DejaVuSansMono-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationMono-Bold.ttf",
        "/System/Library/Fonts/Menlo.ttc",
        "C:/Windows/Fonts/courbd.ttf",
    ],
}


# ── Comic / cartoon ───────────────────────────────────────────────────────────
COMIC_PARAMS = {
    "bilateral_d":           9,
    "bilateral_sigma_color": 80,
    "bilateral_sigma_space": 80,
    "bilateral_passes":      2,
    "quantise_levels":       6,
    "edge_block_size":       9,    # must be odd
    "edge_C":                2,
    "canny_low":             50,
    "canny_high":            150,
    "edge_dilate_px":        2,
    "edge_close_iter":       1,
    "saturation":            1.45,
    "process_max_px":        1600,
}


# ── Pixel / retro ─────────────────────────────────────────────────────────────
PIXEL_PARAMS = {
    "block_size":        3,     # smaller blocks = clearer faces
    "palette_levels":    12,    # enough colors to keep skin/clothes readable
    "scanline_every":    3,     # subtle CRT effect
    "scanline_alpha":    16,    # light scanlines only
    "vignette_strength": 0.0,   # no darkened corners/background
    "contrast":          1.14,
    "saturation":        1.18,
    "sharpness":         1.28,
    "process_max_px":    0,     # don't shrink before processing
}
# ── Pencil sketch ─────────────────────────────────────────────────────────────
SKETCH_PARAMS = {
    "blur_ksize":      23,   # a bit stronger sketch shading
    "detail_blend":    0.26, # enough face detail, but less than before
    "contrast":        1.58,
    "brightness":      1.08,
    "sharpness":       1.48,
    "edge_strength":   0.38, # stronger sketch lines, but not overpowering
    "threshold":       0,
}


# =============================================================================
# SHARED INTERNAL HELPERS
# =============================================================================


def _load_font(paths, size):
    """Try each path; fall back to Pillow built-in default."""
    for p in paths:
        try:
            return ImageFont.truetype(p, size)
        except (IOError, OSError):
            pass
    return ImageFont.load_default()


def _text_size(draw, text, font):
    """Return (width, height) — compatible with Pillow < 9.2 and >= 9.2."""
    try:
        bb = draw.textbbox((0, 0), text, font=font)
        return bb[2] - bb[0], bb[3] - bb[1]
    except AttributeError:
        return draw.textsize(text, font=font)


def _quantise_pil(img, levels):
    from PIL import ImageOps
    bits = max(1, min(8, int(levels).bit_length()))
    return ImageOps.posterize(img, bits)


def _quantise_np(arr, levels):
    step = 256 // levels
    return (arr // step * step + step // 2).astype(np.uint8)


def _make_comic_edges(gray, p):
    """uint8 mask: 0 = edge, 255 = fill. Requires cv2."""
    edges = cv2.adaptiveThreshold(
        gray, 255,
        cv2.ADAPTIVE_THRESH_MEAN_C,
        cv2.THRESH_BINARY,
        p["edge_block_size"], p["edge_C"],
    )
    if p["canny_low"] > 0:
        canny = cv2.Canny(gray, p["canny_low"], p["canny_high"])
        edges = cv2.bitwise_and(edges, cv2.bitwise_not(canny))
    if p["edge_dilate_px"] > 0:
        k = cv2.getStructuringElement(
            cv2.MORPH_ELLIPSE,
            (p["edge_dilate_px"] * 2 + 1,) * 2,
        )
        edges = cv2.erode(edges, k)
    if p["edge_close_iter"] > 0:
        ck = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (3, 3))
        edges = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, ck,
                                 iterations=p["edge_close_iter"])
    return edges


def _radial_vignette(img_rgb, strength):
    """Darken corners with a smooth radial gradient. Returns RGB PIL image."""
    if strength <= 0:
        return img_rgb
    W, H  = img_rgb.size
    mask  = Image.new("L", (W, H), 0)
    d     = ImageDraw.Draw(mask)
    steps = 48
    for i in range(steps):
        frac  = i / steps
        level = int(255 * (1.0 - frac * strength))
        x0    = int(W * frac * 0.5)
        y0    = int(H * frac * 0.5)
        d.ellipse([x0, y0, W - x0, H - y0], fill=level)
    mask = Image.eval(mask, lambda v: 255 - v)
    dark = Image.new("RGB", (W, H), (0, 0, 0))
    return Image.composite(dark, img_rgb, mask)


# =============================================================================
# FILTER 1 — BRANDING OVERLAY
# =============================================================================


def _fit_font(draw, text, paths, start_size, max_width, min_size=10):
    """Return the largest loaded font that fits text inside max_width."""
    text = str(text or "").strip()
    size = max(min_size, int(start_size))
    while size > min_size:
        font = _load_font(paths, size)
        w, _ = _text_size(draw, text, font)
        if w <= max_width:
            return font
        size -= 2
    return _load_font(paths, min_size)


def apply_branding_overlay(pil_img, params=None):
    """
    Append a full-width DUEN-branded footer below the image.


    This version makes the event title the main readable text. The previous
    footer treated the event name like a tiny subtitle, which became almost
    unreadable once the final image was resized to 384px for thermal printing.
    """
    p = {**BRANDING_PARAMS, **(params or {})}
    src = pil_img.convert("RGB")
    W, H = src.size


    footer_h = int(H * p["header_height_frac"])
    footer_h = max(p["header_min_px"], min(p["header_max_px"], footer_h))
    rule_h = p["rule_height"]


    footer = Image.new("RGB", (W, footer_h), p["bg_color"])
    d = ImageDraw.Draw(footer)


    d.rectangle([0, 0, W, rule_h], fill=p["accent_color"])


    pad_x = max(12, int(W * 0.035))
    fps = p["font_paths"]


    duen_text = str(p.get("title", "DUEN") or "DUEN").strip()
    event_text = str(p.get("event_subtitle", "") or "").strip() or "PANORAMA EVENT"
    ts_text = datetime.now().strftime(p["timestamp_fmt"])


    # Small DUEN label at top-left.
    f_duen = _load_font(fps, max(12, int(footer_h * p.get("duen_font_frac", 0.22))))
    duen_w, duen_h = _text_size(d, duen_text, f_duen)
    d.text((pad_x, max(rule_h + 6, int(footer_h * 0.12))),
           duen_text, font=f_duen, fill=p["title_color"])


    # Big centered event title. Shrink only if the title is too long.
    max_event_w = W - 2 * pad_x
    f_event = _fit_font(
        d, event_text, fps,
        max(18, int(footer_h * p.get("event_font_frac", 0.42))),
        max_event_w,
        min_size=max(12, int(footer_h * 0.22)),
    )
    event_w, event_h = _text_size(d, event_text, f_event)
    event_x = max(pad_x, (W - event_w) // 2)
    event_y = max(rule_h + duen_h + 10, int(footer_h * 0.40))
    if event_y + event_h > footer_h - 18:
        event_y = max(rule_h + 8, (footer_h - event_h) // 2)
    d.text((event_x, event_y), event_text, font=f_event, fill=p["subtitle_color"])


    # Timestamp is intentionally small and secondary.
    f_ts = _load_font(fps, max(8, int(footer_h * p.get("timestamp_font_frac", 0.14))))
    ts_w, ts_h = _text_size(d, ts_text, f_ts)
    d.text((W - ts_w - pad_x, footer_h - ts_h - 8),
           ts_text, font=f_ts, fill=p["timestamp_color"])


    out = Image.new("RGB", (W, H + footer_h))
    out.paste(src, (0, 0))
    out.paste(footer, (0, H))
    return out


# =============================================================================
# FILTER 2 — COMIC / CARTOON
# =============================================================================
def apply_comic_filter(pil_img, params=None):
    """
    Sharper comic/cartoon effect.


    Less blur than the previous version:
    - fewer bilateral filter passes
    - stronger facial detail preservation
    - sharper edge mask
    - less aggressive color flattening
    """
    p = {**COMIC_PARAMS, **(params or {})}
    src = pil_img.convert("RGB")


    if not _HAVE_CV2:
        img = src.filter(ImageFilter.MedianFilter(size=3))
        img = ImageEnhance.Color(img).enhance(1.45)
        img = ImageEnhance.Contrast(img).enhance(1.2)
        img = _quantise_pil(img, 5)
        return img


    img_rgb = np.array(src)
    original_h, original_w = img_rgb.shape[:2]


    # Optional resize only for speed, not too small
    process_max_px = 1800
    working = img_rgb.copy()


    if max(original_w, original_h) > process_max_px:
        scale = process_max_px / max(original_w, original_h)
        new_w = int(original_w * scale)
        new_h = int(original_h * scale)
        working = cv2.resize(working, (new_w, new_h), interpolation=cv2.INTER_AREA)


    h, w = working.shape[:2]


    # 1. Light smoothing, not heavy blur
    img_color = working.copy()


    for _ in range(3):
        img_color = cv2.bilateralFilter(
            img_color,
            d=7,
            sigmaColor=45,
            sigmaSpace=45
        )


    # 2. Slight color flattening, but keep facial details
    levels = 7
    step = 256 // levels
    img_color = (img_color // step * step + step // 2).astype(np.uint8)


    # 3. Sharpen the color image so faces are less blurry
    gaussian = cv2.GaussianBlur(img_color, (0, 0), 1.0)
    img_color = cv2.addWeighted(img_color, 1.45, gaussian, -0.45, 0)


    # 4. Edge detection from original working image
    img_gray = cv2.cvtColor(working, cv2.COLOR_RGB2GRAY)


    # Smaller blur keeps facial features
    img_gray_blur = cv2.medianBlur(img_gray, 3)


    img_edge = cv2.adaptiveThreshold(
        img_gray_blur,
        255,
        cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY,
        blockSize=7,
        C=3
    )


    # 5. Add Canny edges for facial features like eyes, nose, mouth
    canny = cv2.Canny(img_gray_blur, 40, 120)
    canny_inv = cv2.bitwise_not(canny)


    img_edge = cv2.bitwise_and(img_edge, canny_inv)


    # 6. Do NOT thicken edges too much
    # This keeps faces readable instead of turning them into blobs
    kernel = np.ones((1, 1), np.uint8)
    img_edge = cv2.erode(img_edge, kernel, iterations=1)


    img_edge = cv2.cvtColor(img_edge, cv2.COLOR_GRAY2RGB)


    # 7. Combine color + edges
    cartoon = cv2.bitwise_and(img_color, img_edge)


    # 8. Resize back if needed
    if cartoon.shape[1] != original_w or cartoon.shape[0] != original_h:
        cartoon = cv2.resize(cartoon, (original_w, original_h), interpolation=cv2.INTER_CUBIC)


    out = Image.fromarray(cartoon)


    # 9. Final clean enhancement
    out = ImageEnhance.Color(out).enhance(1.35)
    out = ImageEnhance.Contrast(out).enhance(1.2)
    out = ImageEnhance.Sharpness(out).enhance(1.6)


    return out
# =============================================================================
# FILTER 3 — PIXEL / RETRO
# =============================================================================


def apply_pixel_filter(pil_img, params=None):
    """
    Retro / 90s game pixel filter with clearer faces.


    Goals:
    - obvious pixelated / retro style
    - faces still relatively clear
    - subtle CRT vibe
    - no blackened background
    """
    p = {**PIXEL_PARAMS, **(params or {})}
    src = pil_img.convert("RGB")
    W, H = src.size


    block = max(1, p["block_size"])


    # Downscale gently so it looks pixelated but still readable
    small_w = max(1, W // block)
    small_h = max(1, H // block)
    small = src.resize((small_w, small_h), Image.BOX)


    # Reduce colors for retro feel, but keep enough tones for faces
    if _HAVE_CV2:
        arr = np.array(small)
        arr = _quantise_np(arr, p["palette_levels"])
        small = Image.fromarray(arr)
    else:
        small = _quantise_pil(small, p["palette_levels"])


    # Upscale with sharp pixel edges
    pixelated = small.resize((W, H), Image.NEAREST)


    # Light scanlines for 90s CRT feel
    if p["scanline_every"] > 0 and p["scanline_alpha"] > 0:
        alpha = p["scanline_alpha"]
        scan_layer = Image.new("RGBA", (W, H), (0, 0, 0, 0))
        sd = ImageDraw.Draw(scan_layer)


        for y in range(0, H, p["scanline_every"]):
            sd.line([(0, y), (W, y)], fill=(0, 0, 0, alpha))


        pixelated = pixelated.convert("RGBA")
        pixelated = Image.alpha_composite(pixelated, scan_layer).convert("RGB")


    # No darkened background unless explicitly enabled
    if p["vignette_strength"] > 0:
        pixelated = _radial_vignette(pixelated, p["vignette_strength"])


    # Mild enhancement to keep people readable
    pixelated = ImageEnhance.Contrast(pixelated).enhance(p["contrast"])
    pixelated = ImageEnhance.Color(pixelated).enhance(p["saturation"])
    pixelated = ImageEnhance.Sharpness(pixelated).enhance(p["sharpness"])


    return pixelated
# =============================================================================
# FILTER 4 — SKETCH FILTER
# =============================================================================
def apply_sketch_filter(pil_img, params=None):
    """
    Pencil sketch filter tuned for a middle ground:
    - clearly sketch-like
    - still readable faces
    - good for thermal printing
    """
    p = {**SKETCH_PARAMS, **(params or {})}
    src = pil_img.convert("RGB")


    # Pillow-only fallback
    if not _HAVE_CV2:
        gray = ImageEnhance.Color(src).enhance(0.0).convert("L")
        gray = gray.filter(ImageFilter.EDGE_ENHANCE_MORE)
        gray = ImageEnhance.Contrast(gray).enhance(p["contrast"])
        gray = ImageEnhance.Brightness(gray).enhance(p["brightness"])
        gray = ImageEnhance.Sharpness(gray).enhance(p["sharpness"])
        return gray.convert("RGB")


    # 1. Grayscale
    gray = cv2.cvtColor(np.array(src), cv2.COLOR_RGB2GRAY)


    # 2. Gentle smoothing
    gray_clean = cv2.bilateralFilter(gray, d=5, sigmaColor=40, sigmaSpace=40)


    # 3. Local contrast boost for faces/features
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    gray_detail = clahe.apply(gray_clean)


    # 4. Classic pencil sketch dodge blend
    inv = 255 - gray_detail


    ksize = int(p["blur_ksize"])
    if ksize < 3:
        ksize = 3
    if ksize % 2 == 0:
        ksize += 1


    blur = cv2.GaussianBlur(inv, (ksize, ksize), 0)


    denom = 255 - blur
    denom[denom == 0] = 1
    sketch = cv2.divide(gray_detail, denom, scale=256)


    # 5. Edge extraction for artistic sketch lines
    edges = cv2.Canny(gray_detail, 55, 145)
    edges = cv2.GaussianBlur(edges, (3, 3), 0)
    edges = cv2.dilate(edges, np.ones((1, 1), np.uint8), iterations=1)
    edge_layer = 255 - edges  # black lines on white background


    # 6. Blend in edge layer
    edge_strength = float(p["edge_strength"])
    sketch = cv2.addWeighted(
        sketch, 1.0 - edge_strength,
        edge_layer, edge_strength,
        0
    )


    # 7. Blend back some original detail so faces stay readable
    detail_blend = float(p["detail_blend"])
    if detail_blend > 0:
        sketch = cv2.addWeighted(
            sketch, 1.0 - detail_blend,
            gray_detail, detail_blend,
            0
        )


    # 8. Optional threshold if ever needed
    threshold = int(p["threshold"])
    if threshold > 0:
        _, sketch = cv2.threshold(sketch, threshold, 255, cv2.THRESH_BINARY)


    # 9. Mild sharpening for final facial clarity
    blurred = cv2.GaussianBlur(sketch, (0, 0), 0.8)
    sketch = cv2.addWeighted(sketch, 1.28, blurred, -0.28, 0)


    out = Image.fromarray(sketch).convert("RGB")
    out = ImageEnhance.Contrast(out).enhance(p["contrast"])
    out = ImageEnhance.Brightness(out).enhance(p["brightness"])
    out = ImageEnhance.Sharpness(out).enhance(p["sharpness"])


    return out


# =============================================================================
# MASTER DISPATCHER
# =============================================================================


def apply_filter(pil_img, filter_key, **kwargs):
    """
    Apply the filter named by *filter_key* to *pil_img*.


    filter_key : "none" | "branding" | "comic" | "pixel" | "cctv"
    **kwargs   : override keys from the corresponding *_PARAMS dict.


    Returns a PIL.Image (RGB).
    """
    key = (filter_key or "none").strip().lower()


    if key == "branding":
        return apply_branding_overlay(pil_img, params=kwargs or None)
    if key == "comic":
        return apply_comic_filter(pil_img, params=kwargs or None)
    if key == "pixel":
        return apply_pixel_filter(pil_img, params=kwargs or None)
    if key == "sketch":
        return apply_sketch_filter(pil_img, params=kwargs or None)
    # "none" or unrecognised — return a clean RGB copy
    return pil_img.convert("RGB")


# =============================================================================
# SMOKE-TEST   python3 photo_filters.py <image> [branding|comic|pixel|cctv|all]
# =============================================================================
if __name__ == "__main__":
    import sys


    if len(sys.argv) < 2:
        print("Usage: python3 photo_filters.py <image> [branding|comic|pixel|sketch|all]")
        sys.exit(1)


    src_path   = sys.argv[1]
    filter_arg = sys.argv[2].lower() if len(sys.argv) > 2 else "all"
    src        = Image.open(src_path).convert("RGB")
    base, ext  = os.path.splitext(src_path)


    run_keys = [k for k in FILTERS if k != "none"] if filter_arg == "all" else [filter_arg]


    for key in run_keys:
        print(f"Applying '{key}' to {src_path}  ({src.size[0]}x{src.size[1]}) ...")
        result   = apply_filter(src.copy(), key)
        out_path = f"{base}_{key}{ext or '.png'}"
        result.save(out_path)
        print(f"  -> {out_path}")



