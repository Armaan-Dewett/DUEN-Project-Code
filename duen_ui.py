# DUEN UI only
# This file does not redefine motor/camera/stitch/print settings.
# It imports hardware logic from duen_hardware.py and calls it.
# Run on Raspberry Pi:
#   sudo pigpiod
#   sudo python3 duen_ui.py
# Optional desktop/dev mode:
#   DUEN_HEADLESS=1 python3 duen_ui.py


#code


# DO NOT TOUCH
# DO NOT TOUCH
# DO NOT TOUCH
# DO NOT TOUCH
# DO NOT TOUCH
# DO NOT TOUCH
# DO NOT TOUCH
# DO NOT TOUCH
# DO NOT TOUCH
# DO NOT TOUCH
# DO NOT TOUCH
# DO NOT TOUCH
# DO NOT TOUCH
# DO NOT TOUCH
# DO NOT TOUCH
# DO NOT TOUCH
# DO NOT TOUCH
# DO NOT TOUCH
# DO NOT TOUCH
# DO NOT TOUCH
# DO NOT TOUCH
# DO NOT TOUCH
# DO NOT TOUCH
# DO NOT TOUCH
# DO NOT TOUCH
# DO NOT TOUCH
# DO NOT TOUCH


import os, threading, time
from datetime import datetime
import tkinter as tk
from tkinter import font as tkfont
from PIL import Image, ImageTk, ImageDraw


import duen_hardware as hw


# Ã¢â€â‚¬Ã¢â€â‚¬ PALETTE + LAYOUT CONSTANTS Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬
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


W, H         = 800, 480
FEED_W       = 560
SIDE_W       = W - FEED_W
TOTAL_ANGLES = len(hw.ANGLES_TO_CAPTURE)  # auto-updates when hardware angle list changes


class BoothUI(tk.Tk):
 def __init__(self):
     super().__init__()
     self.title("DUEN Booth v2")
     self.geometry(f"{W}x{H}")
     self.resizable(False, False)
     self.configure(bg=BG)
     self.attributes("-fullscreen", False)  # disabled for UI test


     self.f_mono_lg = tkfont.Font(family="Courier", size=14, weight="bold")
     self.f_mono_md = tkfont.Font(family="Courier", size=11)
     self.f_mono_sm = tkfont.Font(family="Courier", size=9)
     self.f_sans_lg = tkfont.Font(family="DejaVu Sans", size=13, weight="bold")
     self.f_sans_md = tkfont.Font(family="DejaVu Sans", size=11)
     self.f_sans_sm = tkfont.Font(family="DejaVu Sans", size=9)


     self._photo_ref = None
     self._logo_img  = None
     self._is_running = False
     self._stop_requested = False


     self.print_total    = hw.load_print_total()
     self.print_session  = 0
     self.countdown_secs = hw.DEFAULT_COUNTDOWN
     self.pending_copies = hw.DEFAULT_PRINTS


     self._preview_running  = True
     self._preview_active   = True
     self._preview_photo    = None
     self._countdown_value  = None
     self._latest_still_pil = None
     self._pan_running      = False


     self._customize_overlay = None
     self._auto_exposure = True   # mirrors hw.USE_AUTO_EXPOSURE
     self._capture_mode = "panorama"   # "panorama" or "still"
     self._selected_filter_key = getattr(hw, "CURRENT_FILTER_KEY", "none")
     self._event_title = getattr(hw, "CURRENT_EVENT_TITLE", "PANORAMA EVENT")
     self._touch_keyboard = None
     self._touch_keyboard_entry = None
     self._touch_keyboard_var = None


     # Loading overlay state (stitching screen)
     self._loading_overlay        = None
     self._loading_animating      = False
     self._light_mode_before_load = None  # restore mode after loading
     self._picker_overlay    = None
     self._picker_thumb_ref  = None
     self._qr_overlay = None
     self._auto_flash_enabled = getattr(hw, "AUTO_FLASH_ENABLED", True)
     self._last_print_path = None
     self._last_print_qr_path = None
     self._last_print_had_qr = False


     self.lights = hw.LightController(hw.strip)
     self.lights.set_mode(hw.DEFAULT_MODE)
     self.lights.set_brightness(0)
     self.light_mode = hw.DEFAULT_MODE


     self._build()
     self._update_clock()
     self._poll_ui()


     hw.button.when_pressed          = self._on_button_press
     hw.settings_button.when_pressed = self._on_settings_button_press


     # GPIO 6 â€” physical customize button (opens / closes the overlay).
     # Uses self.after(0, ...) so the gpiozero background thread never
     # touches Tkinter directly.
     self._customize_btn_last = 0.0
     try:
         from gpiozero import Button as _CustBtn
         self._customize_btn = _CustBtn(16, pull_up=True, bounce_time=0.05)
         self._customize_btn.when_pressed = (
             lambda: self.after(1, self._on_customize_btn_press)
         )
     except Exception as _cb_err:
         print(f"[customize button] could not wire GPIO 16: {_cb_err}")


      # GPIO 5 - physical emergency reset button.
     # Fires _on_reset regardless of running state � intentional emergency stop.
     # Uses self.after(0, ...) so gpiozero background thread never touches Tkinter.
     try:
         from gpiozero import Button as _ResetBtn
         self._reset_btn = _ResetBtn(5, pull_up=True, bounce_time=0.05)
         self._reset_btn.when_pressed = (
             lambda: self.after(0, self._on_reset)
         )
     except Exception as _rb_err:
         print(f"[reset button] could not wire GPIO 5: {_rb_err}")


      # GPIO 24 - physical preview pan button.
     # Ignored while a capture is already running, same as the old UI button.
     # Uses self.after(0, ...) so gpiozero background thread never touches Tkinter.
     try:
         from gpiozero import Button as _PanBtn
         self._pan_btn = _PanBtn(24, pull_up=True, bounce_time=0.05)
         self._pan_btn.when_pressed = (
             lambda: self.after(0, self._on_preview_pan)
         )
     except Exception as _pb_err:
         print(f"[pan button] could not wire GPIO 24: {_pb_err}")
     self._preview_thread = threading.Thread(
         target=self._preview_loop, daemon=True
     )
     self._preview_thread.start()
     self.bind("<Escape>", lambda e: self._quit())
  # GPIO 23 � physical capture button.
    # Triggers still or panorama depending on _capture_mode.
    # Uses self.after(0, ...) so gpiozero background thread never touches Tkinter.
     try:
         from gpiozero import Button as _CapBtn
         self._capture_btn = _CapBtn(23, pull_up=True, bounce_time=0.05)
         self._capture_btn.when_pressed = (
             lambda: self.after(0, self._on_trigger)
         )
     except Exception as _capb_err:
         print(f"[capture button] could not wire GPIO 23: {_capb_err}")


 def _build(self):
     bar = tk.Frame(self, bg=SURFACE, height=36)
     bar.pack(fill="x", side="top")
     bar.pack_propagate(False)


     logo_loaded = False
     try:
         logo = Image.open(hw.LOGO_PATH)
         logo.thumbnail((140, 28), Image.LANCZOS)
         self._logo_img = ImageTk.PhotoImage(logo)
         tk.Label(bar, image=self._logo_img, bg=SURFACE).pack(side="left", padx=14)
         logo_loaded = True
     except Exception:
         pass
     if not logo_loaded:
         tk.Label(bar, text="DUEN", font=self.f_mono_lg,
                  bg=SURFACE, fg=ACCENT2).pack(side="left", padx=14)


     self.lbl_clock = tk.Label(bar, text="---", font=self.f_mono_sm,
                               bg=SURFACE, fg=MUTED)
     self.lbl_clock.pack(side="right", padx=14)


     self.lbl_phase_pill = tk.Label(bar, text="  READY  ", font=self.f_mono_sm,
                                    bg="#0d2b1a", fg=GREEN,
                                    relief="flat", padx=6, pady=2)
     self.lbl_phase_pill.pack(side="right", padx=6)


     body = tk.Frame(self, bg=BG)
     body.pack(fill="both", expand=True)


     feed = tk.Frame(body, bg="#050507", width=FEED_W)
     feed.pack(side="left", fill="both", expand=True)
     feed.pack_propagate(False)


     self.feed_canvas = tk.Canvas(feed, bg="#050507", highlightthickness=0)
     self.feed_canvas.pack(fill="both", expand=True)
     self.feed_canvas.bind("<Configure>", self._on_feed_resize)


     fbar = tk.Frame(feed, bg=SURFACE, height=24)
     fbar.pack(fill="x", side="bottom")
     fbar.pack_propagate(False)
     self.lbl_feed_info = tk.Label(fbar, text="Live feed",
                                   font=self.f_mono_sm, bg=SURFACE, fg=MUTED)
     self.lbl_feed_info.pack(side="left", padx=10)


     side = tk.Frame(body, bg=SURFACE, width=SIDE_W)
     side.pack(side="right", fill="y")
     side.pack_propagate(False)
     self._build_sidebar(side)


     foot = tk.Frame(self, bg=SURFACE, height=26)
     foot.pack(fill="x", side="bottom")
     foot.pack_propagate(False)
     tk.Label(foot, text="GPIO 26 Ã‚Â· Servo    GPIO 17 Ã‚Â· Button    GPIO 18 Ã‚Â· Ring",
              font=self.f_mono_sm, bg=SURFACE, fg=FAINT).pack(side="left", padx=12)
     self.lbl_last = tk.Label(foot, text="---", font=self.f_mono_sm,
                              bg=SURFACE, fg=MUTED)
     self.lbl_last.pack(side="right", padx=12)


 def _build_sidebar(self, parent):
     tk.Label(parent, text="STATUS", font=self.f_mono_sm,
              bg=SURFACE, fg=FAINT).pack(anchor="w", padx=12, pady=(8, 2))


     self.lbl_phase = tk.Label(parent, text="Idle", font=self.f_sans_lg,
                               bg=SURFACE, fg=TEXT)
     self.lbl_phase.pack(anchor="w", padx=12)


     self.lbl_msg = tk.Label(parent, text="Ready. Press START.",
                             font=self.f_sans_sm, bg=SURFACE, fg=MUTED,
                             wraplength=SIDE_W - 24, justify="left")
     self.lbl_msg.pack(anchor="w", padx=12, pady=(2, 4))


     prog_frame = tk.Frame(parent, bg=SURFACE)
     prog_frame.pack(fill="x", padx=12, pady=(0, 2))
     self.prog_track = tk.Frame(prog_frame, bg=BORDER, height=4)
     self.prog_track.pack(fill="x")
     self.prog_track.pack_propagate(False)
     self.prog_bar = tk.Frame(self.prog_track, bg=ACCENT, height=4, width=0)
     self.prog_bar.place(x=0, y=0, height=4)


     self.lbl_prog = tk.Label(parent, text="", font=self.f_mono_sm,
                              bg=SURFACE, fg=MUTED)
     self.lbl_prog.pack(anchor="w", padx=12)


     tk.Label(parent, text="CAPTURES", font=self.f_mono_sm,
              bg=SURFACE, fg=FAINT).pack(anchor="w", padx=12, pady=(6, 2))


     dot_frame = tk.Frame(parent, bg=SURFACE)
     dot_frame.pack(anchor="w", padx=12)
     self._dots = []
     for i in range(TOTAL_ANGLES):
         d = tk.Frame(dot_frame, bg=FAINT, width=14, height=14)
         d.grid(row=i // 6, column=i % 6, padx=2, pady=2)
         d.grid_propagate(False)
         self._dots.append(d)


     tk.Frame(parent, bg=BORDER, height=1).pack(fill="x", padx=0, pady=6)


     self.btn_start = tk.Button(
        parent, text="START",
        font=self.f_mono_lg, bg=GREEN, fg="#06140a",
        activebackground="#5be88d", activeforeground="#06140a",
        relief="flat", cursor="hand2", pady=12,
        command=self._on_trigger,
      )
     self.btn_start.pack(fill="x", padx=12, pady=(0, 6))


     self.btn_preview_pan = tk.Button(
        parent, text="PREVIEW PANORAMA",
        font=self.f_mono_md, bg=ACCENT, fg="white",
        activebackground=ACCENT2, activeforeground="white",
        relief="flat", cursor="hand2", pady=9,
        command=self._on_preview_pan,
      )
     self.btn_preview_pan.pack(fill="x", padx=12, pady=(0, 6))


     self.btn_customize = tk.Button(
        parent, text="CUSTOMIZE",
        font=self.f_mono_md, bg=SURFACE2, fg=TEXT,
        activebackground=BORDER, activeforeground=TEXT,
        relief="flat", cursor="hand2", pady=9,
        command=self._toggle_customize_overlay,
      )
     self.btn_customize.pack(fill="x", padx=12, pady=(0, 8))


     self.btn_trigger = tk.Button(
        parent, text="   MODE: PANORAMA",
        font=self.f_mono_md, bg=ACCENT, fg="white",
        activebackground=ACCENT2, activeforeground="white",
        relief="flat", cursor="hand2", pady=8,
        command=self._toggle_capture_mode,
      )
     self.btn_trigger.pack(fill="x", padx=12, pady=(0, 4))


     tk.Frame(parent, bg=BORDER, height=1).pack(fill="x", padx=0, pady=8)


     sess_row = tk.Frame(parent, bg=SURFACE)
     sess_row.pack(fill="x", padx=12, pady=1)
     tk.Label(sess_row, text="Session", font=self.f_sans_sm,
              bg=SURFACE, fg=MUTED).pack(side="left")
     self.lbl_session = tk.Label(sess_row, text=str(self.print_session),
                                 font=self.f_mono_md, bg=SURFACE, fg=TEXT)
     self.lbl_session.pack(side="right")


     tot_row = tk.Frame(parent, bg=SURFACE)
     tot_row.pack(fill="x", padx=12, pady=1)
     tk.Label(tot_row, text="Total", font=self.f_sans_sm,
              bg=SURFACE, fg=MUTED).pack(side="left")
     self.lbl_total = tk.Label(tot_row, text=str(self.print_total),
                               font=self.f_mono_md, bg=SURFACE, fg=TEXT)
     self.lbl_total.pack(side="right")


 # Ã¢â€â‚¬Ã¢â€â‚¬ Feed canvas Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬
 def _on_feed_resize(self, event):
     self._feed_w = event.width
     self._feed_h = event.height


 def _show_image(self, pil_img):
     self._latest_still_pil = pil_img
     self._render_feed(None)


 def _render_feed(self, pil_img):
     fw = getattr(self, "_feed_w", FEED_W)
     fh = getattr(self, "_feed_h", H - 36 - 24 - 26)
     src = self._latest_still_pil if self._latest_still_pil is not None else pil_img
     c = self.feed_canvas
     c.delete("all")


     # Force the visible image area to be 4:4 / 1:1.
     # The canvas itself can still be wider, but the camera image is drawn
     # inside a centered square viewport so 1920x1920 captures look square.
     view_size = max(1, min(fw, fh))
     vx0 = (fw - view_size) // 2
     vy0 = (fh - view_size) // 2
     vx1 = vx0 + view_size
     vy1 = vy0 + view_size
     cx = vx0 + view_size // 2
     cy = vy0 + view_size // 2


     # Subtle square display background/border.
     c.create_rectangle(vx0, vy0, vx1, vy1, fill="#050507", outline=BORDER, width=1)


     if src is not None:
         img = src.copy()
         img.thumbnail((view_size, view_size), Image.LANCZOS)
         self._preview_photo = ImageTk.PhotoImage(img)
         c.create_image(cx, cy, image=self._preview_photo, anchor="center")


     m = 10
     corner = 14
     for x1, y1, x2, y2 in [
         (vx0+m, vy0+m, vx0+m+corner, vy0+m),
         (vx0+m, vy0+m, vx0+m, vy0+m+corner),
         (vx1-m, vy0+m, vx1-m-corner, vy0+m),
         (vx1-m, vy0+m, vx1-m, vy0+m+corner),
         (vx0+m, vy1-m, vx0+m+corner, vy1-m),
         (vx0+m, vy1-m, vx0+m, vy1-m-corner),
         (vx1-m, vy1-m, vx1-m-corner, vy1-m),
         (vx1-m, vy1-m, vx1-m, vy1-m-corner),
     ]:
         c.create_line(x1, y1, x2, y2, fill=ACCENT, width=1)


     if self._countdown_value is not None:
         r = min(100, view_size // 4)
         c.create_oval(cx - r, cy - r, cx + r, cy + r,
                       fill="#000000", outline=ACCENT2, width=3)
         c.create_text(cx, cy+40, text=str(self._countdown_value),
                       fill="#ffffff", font=("Courier", 160, "bold"), anchor="center")


 def _preview_loop(self):
     period = 1.0 / hw.PREVIEW_FPS_HZ
     synthetic_frame = None
     if hw.HEADLESS:
         synthetic_frame = Image.new("RGB", (hw.PREVIEW_W, hw.PREVIEW_H), (24, 24, 32))
         d = ImageDraw.Draw(synthetic_frame)
         d.rectangle([4, 4, hw.PREVIEW_W - 5, hw.PREVIEW_H - 5],
                     outline=(120, 120, 160), width=2)
         d.text((20, 20), "PREVIEW (headless)", fill=(200, 200, 220))
         d.text((20, 44), "no camera connected", fill=(140, 140, 170))
     while self._preview_running:
         t0 = time.time()
         if self._preview_active and self._latest_still_pil is None:
             try:
                 if hw.HEADLESS:
                     self.after(0, self._render_feed, synthetic_frame.copy())
                 else:
                     with hw.CAMERA_LOCK:
                         yuv = hw.camera.capture_array("lores")
                     bgr = hw.cv2.cvtColor(yuv, hw.cv2.COLOR_YUV2BGR_I420)
                     rgb = hw.cv2.cvtColor(bgr, hw.cv2.COLOR_BGR2RGB)
                     # Mirror ONLY the idle live preview shown on-screen.
                     # Saved/captured photos stay normal because hardware capture is no longer globally flipped.
                     preview_img = Image.fromarray(rgb).transpose(Image.FLIP_LEFT_RIGHT)
                     self.after(0, self._render_feed, preview_img)
             except Exception:
                 pass
         elif self._countdown_value is not None:
             self.after(0, self._render_feed, None)
         time.sleep(max(0.0, period - (time.time() - t0)))


 # Ã¢â€â‚¬Ã¢â€â‚¬ Phase / progress state Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬
 def set_phase(self, phase, msg, step=0, angle=None):
     phase_labels = {
         "idle":      ("Idle",      TEXT,   "  READY  ",    "#0d2b1a", GREEN),
         "capturing": ("Capturing", ACCENT2,"  RUNNING ",   "#1a1530", ACCENT2),
         "stitching": ("Stitching", AMBER,  "  STITCHING ", "#1a1200", AMBER),
         "printing":  ("Printing",  ACCENT, "  PRINTING ",  "#130e2b", ACCENT),
         "done":      ("Done",      GREEN,  "  DONE  ",     "#0d2b1a", GREEN),
         "error":     ("Error",     RED,    "  ERROR  ",    "#2b0d0d", RED),
     }
     label, color, pill_text, pill_bg, pill_fg = phase_labels.get(
         phase, ("Idle", TEXT, "  READY  ", "#0d2b1a", GREEN))
     self.lbl_phase.config(text=label, fg=color)
     self.lbl_msg.config(text=msg)
     self.lbl_phase_pill.config(text=pill_text, bg=pill_bg, fg=pill_fg)


     running = phase in ("capturing", "stitching", "printing")
     control_state = "disabled" if running else "normal"
     self.btn_trigger.config(state=control_state)
     self.btn_start.config(state=control_state)
     self.btn_preview_pan.config(state=control_state)
     self.btn_customize.config(state=control_state)


     if phase == "capturing":
         pct     = step / TOTAL_ANGLES
         track_w = self.prog_track.winfo_width() or (SIDE_W - 24)
         self.prog_bar.place(x=0, y=0, height=4, width=int(track_w * pct))
         angle_str = f"{angle}" if angle is not None else "---"
         self.lbl_prog.config(text=f"{step} / {TOTAL_ANGLES}  .  {angle_str}")
     else:
         self.prog_bar.place(x=0, y=0, height=4, width=0)
         self.lbl_prog.config(text="")


     for i, d in enumerate(self._dots):
         if phase == "capturing":
             d.config(bg=ACCENT if i < step else ACCENT2 if i == step else FAINT)
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


 # Ã¢â€â‚¬Ã¢â€â‚¬ Clock Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬
 def _update_clock(self):
     self.lbl_clock.config(text=datetime.now().strftime("%a %b %d  .  %H:%M:%S"))
     self.after(1000, self._update_clock)


 _blink_state = False
 def _poll_ui(self):
     self.after(500, self._poll_ui)


 # Ã¢â€â‚¬Ã¢â€â‚¬ Actions Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬
 def _on_trigger(self):
     if self._is_running or self._picker_overlay is not None:
         return
     if self._capture_mode == "panorama":
         threading.Thread(target=self._run_booth, daemon=True).start()
     elif self._capture_mode == "still":
         threading.Thread(target=self._run_still, daemon=True).start()
     else:
         threading.Thread(target=self._run_multi, daemon=True).start()


 def _on_button_press(self):
     self.after(0, self._on_trigger)


 def _on_preview_pan(self):
     if self._is_running or self._pan_running:
         return
     threading.Thread(target=self._run_preview_pan, daemon=True).start()


 def _run_preview_pan(self):
     self._pan_running = True


     def ui(fn, *a, **kw):
         self.after(0, lambda: fn(*a, **kw))


     try:
         ui(self.set_phase, "capturing", "Preview pan...", 0)
         ui(self.set_feed_label, "Preview pan")
         self._latest_still_pil = None


         # Determine sweep range based on selected angle, centered at 135
         try:
             selected = int(self._angle_var.get())
         except Exception:
             selected = 270
         half = selected / 2
         pan_start = min(int(135 + half), 265)
         pan_end   = max(int(135 - half), 13)
         duration  = 4.0 * (selected / 270)


         # Quickly move to start position
         hw.move_servo(pan_start)
         time.sleep(0.8)


         # Slowly pan across
         steps = 40
         step_dt = duration / steps
         for i in range(steps + 1):
             angle = pan_start + (pan_end - pan_start) * i / steps
             hw.move_servo(angle)
             time.sleep(step_dt)


         # Return to center
         hw.move_servo(135)
         ui(self.set_phase, "idle", "Pan complete. Ready when you are.")
         ui(self.set_feed_label, "Live feed")
     except Exception as e:
         ui(self.set_phase, "error", f"Pan failed: {e}")
     finally:
         self._pan_running = False
 def _on_reset(self):
     self._stop_requested   = True
     self._is_running       = False   # release any running capture lock
     self._preview_active   = True    # restore live preview
     self._countdown_value  = None
     self._latest_still_pil = None
     self._hide_loading_overlay()
     self._close_customize_overlay()
     self._close_print_picker()
     try:
         hw.move_servo(135)
     except Exception:
         pass
     self.set_phase("idle", "Ready. Press START.")
     self.set_feed_label("Live feed")
     self.set_last_event(f"Reset at {datetime.now().strftime('%H:%M:%S')}")
 def _toggle_capture_mode(self):
     """Cycle btn_trigger through panorama ? still ? multi (photobooth strip)."""
     if self._is_running:
         return
     if self._capture_mode == "panorama":
         self._capture_mode = "still"
         self.btn_trigger.config(text="   MODE: STILL")
     elif self._capture_mode == "still":
         self._capture_mode = "multi"
         self.btn_trigger.config(text="   MODE: PHOTO STRIP")
     else:
         self._capture_mode = "panorama"
         self.btn_trigger.config(text="   MODE: PANORAMA")


 def _run_still(self):
    if self._is_running:
        return
    self._is_running = True
    self._stop_requested = False


    def ui(fn, *a, **kw):
        self.after(0, lambda: fn(*a, **kw))


    try:
        ui(self._close_customize_overlay)
        self._latest_still_pil = None


        # Countdown (reuses the same countdown logic as panorama)
        if self.countdown_secs > 0:
            for n in range(self.countdown_secs, 0, -1):
                if self._stop_requested:
                    return
                ui(self.set_phase, "capturing", f"Get ready... {n}", 0)
                self._countdown_value = n
                self.lights.flash_once(brightness_fraction=0.30, duration_s=0.15)
                time.sleep(max(0.0, 1.0 - 0.15))
            self._countdown_value = None


        if self._stop_requested:
            return


        ui(self.set_phase, "capturing", "Taking still photo...", 0)
        self._preview_active = False
        time.sleep(max(0.25, 2.0 / hw.PREVIEW_FPS_HZ))


        # Auto flash checks darkness before the shot.
        auto_flash_was_on = self._prepare_auto_flash()

        # If auto flash did not trigger, still do a quick normal flash.
        # If auto flash triggered, keep the ring on steadily during capture.
        if not auto_flash_was_on:
            self.lights.set_brightness(180)
            self.lights.flash_once(brightness_fraction=1.0, duration_s=0.15)


        # Save to ~/photos with a timestamp filename
        os.makedirs(hw.SAVE_DIR, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        filepath = os.path.join(hw.SAVE_DIR, f"still_{timestamp}.jpg")


        # Reuse take_picture logic � pass dummy angle/index
        try:
            hw.take_still_photo(filepath)
        finally:
            self._restore_auto_flash(auto_flash_was_on)

        filepath = self._apply_selected_filter_to_output(filepath)
        result = filepath


        if filepath and os.path.exists(filepath):
            try:
                ui(self._show_image, Image.open(filepath).copy())
                ui(self.set_feed_label, "Still photo")
            except Exception:
                pass
            ui(self.set_phase, "done", "Choose how many copies to print.")
            ui(self._show_print_picker, filepath)
        else:
            ui(self.set_phase, "error", "Still capture failed.")


    except Exception as e:
        ui(self.set_phase, "error", f"Error: {e}")
    finally:
        self._preview_active  = True
        self._countdown_value = None
        self._is_running      = False
        print("--- Still capture complete ---")


 def _run_multi(self):
     """
     Photobooth strip mode: 4 photos with 5-second countdowns each,
     assembled into a vertical strip with logos top and bottom.
     """
     if self._is_running:
         return
     self._is_running = True
     self._stop_requested = False
     MULTI_COUNTDOWN = 5   # fixed 5-second countdown between each shot
     NUM_SHOTS       = 4


     def ui(fn, *a, **kw):
         self.after(0, lambda: fn(*a, **kw))


     captured_paths = []


     try:
         ui(self._close_customize_overlay)
         self._latest_still_pil = None
         os.makedirs(hw.SAVE_DIR, exist_ok=True)
         timestamp_base = datetime.now().strftime("%Y%m%d_%H%M%S")


         for shot_num in range(1, NUM_SHOTS + 1):
             if self._stop_requested:
                 return


             # 5-second countdown for each shot
             for n in range(MULTI_COUNTDOWN, 0, -1):
                 if self._stop_requested:
                     return
                 ui(self.set_phase, "capturing",
                    f"Photo {shot_num} of {NUM_SHOTS} � get ready... {n}", 0)
                 self._countdown_value = n
                 self.lights.flash_once(brightness_fraction=0.30, duration_s=0.15)
                 time.sleep(max(0.0, 1.0 - 0.15))
             self._countdown_value = None


             if self._stop_requested:
                 return


             # Capture
             ui(self.set_phase, "capturing",
                f"Taking photo {shot_num} of {NUM_SHOTS}...", shot_num - 1)
             self._preview_active = False
             time.sleep(max(0.25, 2.0 / hw.PREVIEW_FPS_HZ))


             auto_flash_was_on = self._prepare_auto_flash()
             if not auto_flash_was_on:
                 self.lights.set_brightness(180)
                 self.lights.flash_once(brightness_fraction=1.0, duration_s=0.15)


             filepath = os.path.join(
                 hw.SAVE_DIR, f"multi_{timestamp_base}_{shot_num}.jpg")
             try:
                 hw.take_still_photo(filepath)
             finally:
                 self._restore_auto_flash(auto_flash_was_on)
             captured_paths.append(filepath)


             # Show the just-captured frame in the feed
             if os.path.exists(filepath):
                 try:
                     ui(self._show_image, Image.open(filepath).copy())
                     ui(self.set_feed_label,
                        f"Shot {shot_num} of {NUM_SHOTS} captured")
                 except Exception:
                     pass


             self._preview_active = True
             # Brief live feed gap between shots so subject can reposition
             if shot_num < NUM_SHOTS:
                 time.sleep(1.0)


         if self._stop_requested:
             return


         # Assemble the strip
         ui(self.set_phase, "capturing", "Composing photo strip...", NUM_SHOTS)
         strip_path = os.path.join(
             hw.SAVE_DIR, f"strip_{timestamp_base}.png")
         hw.make_photobooth_strip(captured_paths, strip_path)
         strip_path = self._apply_selected_filter_to_output(strip_path)


         if os.path.exists(strip_path):
             try:
                 ui(self._show_image, Image.open(strip_path).copy())
                 ui(self.set_feed_label, "Photo strip")
             except Exception:
                 pass
             ui(self.set_phase, "done", "Choose how many copies to print.")
             ui(self._show_print_picker, strip_path)
         else:
             ui(self.set_phase, "error", "Strip assembly failed.")


     except Exception as e:
         ui(self.set_phase, "error", f"Error: {e}")
     finally:
         self._preview_active  = True
         self._countdown_value = None
         self._is_running      = False
         print("--- Multi-still complete ---")
 def _run_multi(self):
     """
     Photo strip mode: 4 photos with 5-second countdowns each,
     assembled into a vertical strip with logos top and bottom.
     """
     if self._is_running:
         return
     self._is_running = True
     self._stop_requested = False
     MULTI_COUNTDOWN = 5
     NUM_SHOTS = 4


     def ui(fn, *a, **kw):
         self.after(0, lambda: fn(*a, **kw))


     captured_paths = []


     try:
         ui(self._close_customize_overlay)
         os.makedirs(hw.SAVE_DIR, exist_ok=True)
         timestamp_base = datetime.now().strftime("%Y%m%d_%H%M%S")


         for shot_num in range(1, NUM_SHOTS + 1):
             if self._stop_requested:
                 return


             # Clear any previous still AND disable preview so countdown
             # overlay renders reliably on every tick
             self._latest_still_pil = None
             self._preview_active   = True


             # 5-second countdown
             for n in range(MULTI_COUNTDOWN, 0, -1):
                 if self._stop_requested:
                     return
                 ui(self.set_phase, "capturing",
                    f"Photo {shot_num} of {NUM_SHOTS} — {n}...", 0)
                 self._countdown_value = n
                 # Fire a render so the countdown number appears immediately
                 self.lights.flash_once(brightness_fraction=0.30, duration_s=0.15)
                 time.sleep(max(0.0, 1.0 - 0.15))


             self._countdown_value = None


             if self._stop_requested:
                 return


             # Capture
             ui(self.set_phase, "capturing",
                f"Taking photo {shot_num} of {NUM_SHOTS}...", shot_num - 1)
             time.sleep(max(0.25, 2.0 / hw.PREVIEW_FPS_HZ))
             auto_flash_was_on = self._prepare_auto_flash()
             if not auto_flash_was_on:
                 self.lights.set_brightness(180)
                 self.lights.flash_once(brightness_fraction=1.0, duration_s=0.15)


             filepath = os.path.join(
                 hw.SAVE_DIR, f"multi_{timestamp_base}_{shot_num}.jpg")
             try:
                 hw.take_still_photo(filepath)
             finally:
                 self._restore_auto_flash(auto_flash_was_on)
             captured_paths.append(filepath)


             # Show the captured frame for 1 second
             if os.path.exists(filepath):
                 try:
                     captured_pil = Image.open(filepath).copy()
                     ui(self._show_image, captured_pil)
                     ui(self.set_feed_label,
                        f"Shot {shot_num} captured — reposition!")
                 except Exception:
                     pass


             # Wait 1.5s showing the captured frame, then clear it
             time.sleep(1.5)
             self._latest_still_pil = None
             self._preview_active   = True


             # Give the camera pipeline time to fully restart after
             # take_still_photo's reconfigure, then show live feed
             # before the next countdown begins
             if shot_num < NUM_SHOTS:
                 time.sleep(3.0)


         if self._stop_requested:
             return


         # Assemble strip
         self._preview_active = False
         ui(self.set_phase, "capturing", "Composing photo strip...", NUM_SHOTS)
         strip_path = os.path.join(hw.SAVE_DIR, f"strip_{timestamp_base}.png")
         hw.make_photobooth_strip(captured_paths, strip_path)
         strip_path = self._apply_selected_filter_to_output(strip_path)


         if os.path.exists(strip_path):
             try:
                 ui(self._show_image, Image.open(strip_path).copy())
                 ui(self.set_feed_label, "Photo strip ready")
             except Exception:
                 pass
             ui(self.set_phase, "done", "Choose how many copies to print.")
             ui(self._show_print_picker, strip_path)
         else:
             ui(self.set_phase, "error", "Strip assembly failed.")


     except Exception as e:
         ui(self.set_phase, "error", f"Error: {e}")
     finally:
         self._preview_active   = True
         self._countdown_value  = None
         self._latest_still_pil = None
         self._is_running       = False
         print("--- Multi-still complete ---")


 def _refresh_counters(self):
     self.lbl_session.config(text=str(self.print_session))
     self.lbl_total.config(text=str(self.print_total))


 # Ã¢â€â‚¬Ã¢â€â‚¬ Customize overlay


 def _on_customize_btn_press(self):
     now = time.time()
     if now - self._customize_btn_last < 1.0:
         return
     self._customize_btn_last = now
     self._toggle_customize_overlay()


 def _on_settings_button_press(self):
     self.after(0, self._toggle_customize_overlay)


 def _toggle_customize_overlay(self):
     if self._is_running or self._picker_overlay is not None:
         return
     if self._customize_overlay is not None:
         self._close_customize_overlay()
     else:
         self._build_customize_overlay()


 def _build_customize_overlay(self):
     overlay = tk.Frame(self, bg=BG)
     overlay.place(x=0, y=0, width=W, height=H)
     self._customize_overlay = overlay


     header = tk.Frame(overlay, bg=BG)
     header.pack(fill="x", pady=(18, 6))
     tk.Label(header, text="CUSTOMIZE", font=self.f_mono_lg,
              bg=BG, fg=ACCENT2).pack(side="left", padx=24)


     tk.Frame(overlay, bg=BORDER, height=1).pack(fill="x", padx=18)


     body = tk.Frame(overlay, bg=BG)
     body.pack(fill="both", expand=True, padx=18, pady=12)


     # Timer column
     timer_col = tk.Frame(body, bg=BG, width=320)
     timer_col.pack(side="left", fill="y", padx=(6, 12))
     timer_col.pack_propagate(False)
     tk.Label(timer_col, text="TIMER", font=self.f_mono_md,
              bg=BG, fg=ACCENT2).pack(anchor="w", pady=(4, 6))
     import tkinter.ttk as ttk
     timer_var = tk.StringVar(value={0:"OFF",3:"3 SECONDS",5:"5 SECONDS",10:"10 SECONDS"}.get(self.countdown_secs, "3 SECONDS"))
     self._timer_var = timer_var
     timer_menu = ttk.Combobox(
         timer_col, textvariable=timer_var,
         values=["OFF", "3 SECONDS", "5 SECONDS", "10 SECONDS"],
         font=self.f_mono_md, state="readonly", width=16,
     )
     timer_menu.pack(anchor="w", pady=(0, 16))
     timer_menu.bind("<<ComboboxSelected>>", lambda e: self._select_countdown(
         {"OFF":0,"3 SECONDS":3,"5 SECONDS":5,"10 SECONDS":10}[self._timer_var.get()]
     ))
     self._timer_btn_refs = {}


     tk.Frame(timer_col, bg=BORDER, height=1).pack(fill="x", pady=(0, 10))
     tk.Label(timer_col, text="PANORAMA ANGLE", font=self.f_mono_md,
              bg=BG, fg=ACCENT2).pack(anchor="w", pady=(0, 6))
     angle_var = tk.StringVar(value="270")
     self._angle_var = angle_var
     angle_menu = ttk.Combobox(
         timer_col, textvariable=angle_var,
         values=["90", "180", "270"],
         font=self.f_mono_md, state="readonly", width=16,
     )
     angle_menu.pack(anchor="w", pady=(0, 6))
     angle_menu.bind("<<ComboboxSelected>>", lambda e: self._select_panorama_angle(
         int(self._angle_var.get())
     ))


     # Filter column
     filter_col = tk.Frame(body, bg=BG, width=180)
     filter_col.pack(side="left", fill="y", padx=(4, 12))
     filter_col.pack_propagate(False)
     tk.Label(filter_col, text="FILTER", font=self.f_mono_md,
              bg=BG, fg=ACCENT2).pack(anchor="w", pady=(4, 6))


     self._filter_btn_refs = {}
     filter_options = [
         ("none", "REGULAR"),
         ("sketch", "SKETCH"),
         ("comic", "COMIC"),
         ("pixel", "RETRO"),
         ("branding", "EVENT TITLE"),
     ]
     for key, label in filter_options:
         btn = tk.Button(
             filter_col, text=label, font=self.f_mono_sm,
             bg=ACCENT if self._selected_filter_key == key else SURFACE2,
             fg="white" if self._selected_filter_key == key else TEXT,
             activebackground=ACCENT2, activeforeground="white",
             relief="flat", cursor="hand2", width=15, pady=6,
             command=lambda k=key: self._select_output_filter(k),
         )
         btn.pack(anchor="w", fill="x", pady=2)
         self._filter_btn_refs[key] = btn


     tk.Label(filter_col, text="Event title text", font=self.f_sans_sm,
              bg=BG, fg=MUTED).pack(anchor="w", pady=(10, 2))
     self._event_title_var = tk.StringVar(value=self._event_title)
     event_entry = tk.Entry(
         filter_col, textvariable=self._event_title_var,
         font=self.f_mono_sm, bg=SURFACE2, fg=TEXT, insertbackground=TEXT,
         relief="flat", width=17,
     )
     event_entry.pack(anchor="w", fill="x", pady=(0, 4), ipady=5)
     event_entry.bind("<KeyRelease>", lambda e: self._update_event_title())
     event_entry.bind("<FocusIn>", lambda e: self._show_touch_keyboard(event_entry, self._event_title_var))
     event_entry.bind("<Button-1>", lambda e: self._show_touch_keyboard(event_entry, self._event_title_var))
     event_entry.bind("<FocusOut>", lambda e: self._update_event_title())


     tk.Button(
         filter_col, text="OPEN KEYBOARD", font=self.f_mono_sm,
         bg=SURFACE2, fg=TEXT, activebackground=ACCENT2, activeforeground="white",
         relief="flat", cursor="hand2", pady=5,
         command=lambda: self._show_touch_keyboard(event_entry, self._event_title_var),
     ).pack(anchor="w", fill="x", pady=(0, 6))


     tk.Label(
         filter_col,
         text="Filters are applied after capture, so they do not mess with stitching.",
         font=self.f_sans_sm, bg=BG, fg=MUTED, wraplength=165, justify="left",
     ).pack(anchor="w", pady=(6, 0))


     # Lights column
     light_col = tk.Frame(body, bg=BG)
     light_col.pack(side="left", fill="both", expand=True, padx=(12, 6))
     tk.Label(light_col, text="LIGHTS", font=self.f_mono_md,
              bg=BG, fg=ACCENT2).pack(anchor="w", pady=(4, 6))


     tk.Label(light_col, text="Brightness", font=self.f_sans_sm,
              bg=BG, fg=MUTED).pack(anchor="w", pady=(8, 0))
     self.scale_brightness = tk.Scale(
         light_col, from_=0, to=255, orient="horizontal",
         bg=BG, fg=TEXT, troughcolor=SURFACE2,
         highlightthickness=0, bd=0, length=300,
         sliderrelief="flat", activebackground=ACCENT2,
         font=self.f_mono_sm, showvalue=True,
         command=self._on_brightness_change,
     )
     self.scale_brightness.set(self.lights.brightness)
     self.scale_brightness.pack(anchor="w", pady=(2, 0))


     # ── Exposure mode ──────────────────────────────────────────────────
     tk.Frame(light_col, bg=BORDER, height=1).pack(fill="x", pady=(14, 8))
     tk.Label(light_col, text="EXPOSURE", font=self.f_mono_md,
              bg=BG, fg=ACCENT2).pack(anchor="w")
     tk.Label(
         light_col,
         text="AUTO: camera meters then locks.\nMANUAL: uses fixed values in hardware file.",
         font=self.f_sans_sm, bg=BG, fg=MUTED, justify="left",
     ).pack(anchor="w", pady=(2, 6))
     exp_row = tk.Frame(light_col, bg=BG)
     exp_row.pack(anchor="w")
     self._btn_exp_auto = tk.Button(
         exp_row, text="AUTO", font=self.f_mono_sm,
         bg=ACCENT if self._auto_exposure else SURFACE2,
         fg="white" if self._auto_exposure else TEXT,
         activebackground=ACCENT2, activeforeground="white",
         relief="flat", cursor="hand2", width=10, pady=8,
         command=lambda: self._set_exposure_mode(True),
     )
     self._btn_exp_auto.pack(side="left", padx=(0, 4))
     self._btn_exp_manual = tk.Button(
         exp_row, text="MANUAL", font=self.f_mono_sm,
         bg=ACCENT if not self._auto_exposure else SURFACE2,
         fg="white" if not self._auto_exposure else TEXT,
         activebackground=ACCENT2, activeforeground="white",
         relief="flat", cursor="hand2", width=10, pady=8,
         command=lambda: self._set_exposure_mode(False),
     )
     self._btn_exp_manual.pack(side="left")


     # ── Auto flash mode ────────────────────────────────────────────────
     tk.Frame(light_col, bg=BORDER, height=1).pack(fill="x", pady=(14, 8))
     tk.Label(light_col, text="AUTO FLASH", font=self.f_mono_md,
              bg=BG, fg=ACCENT2).pack(anchor="w")
     tk.Label(
         light_col,
         text="If the camera sees a dark scene, the ring light turns on before capture.",
         font=self.f_sans_sm, bg=BG, fg=MUTED, justify="left", wraplength=290,
     ).pack(anchor="w", pady=(2, 6))
     flash_row = tk.Frame(light_col, bg=BG)
     flash_row.pack(anchor="w")
     self._btn_flash_on = tk.Button(
         flash_row, text="ON", font=self.f_mono_sm,
         bg=ACCENT if self._auto_flash_enabled else SURFACE2,
         fg="white" if self._auto_flash_enabled else TEXT,
         activebackground=ACCENT2, activeforeground="white",
         relief="flat", cursor="hand2", width=10, pady=8,
         command=lambda: self._set_auto_flash_mode(True),
     )
     self._btn_flash_on.pack(side="left", padx=(0, 4))
     self._btn_flash_off = tk.Button(
         flash_row, text="OFF", font=self.f_mono_sm,
         bg=ACCENT if not self._auto_flash_enabled else SURFACE2,
         fg="white" if not self._auto_flash_enabled else TEXT,
         activebackground=ACCENT2, activeforeground="white",
         relief="flat", cursor="hand2", width=10, pady=8,
         command=lambda: self._set_auto_flash_mode(False),
     )
     self._btn_flash_off.pack(side="left")


 def _show_touch_keyboard(self, entry, text_var):
     """Simple built-in touch keyboard for the event title Entry."""
     self._touch_keyboard_entry = entry
     self._touch_keyboard_var = text_var


     if self._touch_keyboard is not None:
         self._touch_keyboard.lift()
         try:
             entry.focus_set()
         except Exception:
             pass
         return


     kb = tk.Frame(
         self, bg="#050507", bd=0,
         highlightthickness=1, highlightbackground=ACCENT,
     )
     kb.place(x=0, y=H - 190, width=W, height=190)
     kb.lift()
     self._touch_keyboard = kb


     def add_key(parent, label, value=None, width=4):
         tk.Button(
             parent, text=label, font=self.f_mono_sm,
             bg=SURFACE2, fg=TEXT, activebackground=ACCENT2, activeforeground="white",
             relief="flat", cursor="hand2", width=width, pady=5,
             command=lambda v=(value if value is not None else label): self._touch_key_press(v),
         ).pack(side="left", padx=2, pady=2)


     rows = [
         list("1234567890"),
         list("QWERTYUIOP"),
         list("ASDFGHJKL"),
         list("ZXCVBNM"),
     ]


     for row in rows:
         row_frame = tk.Frame(kb, bg="#050507")
         row_frame.pack(anchor="center")
         for ch in row:
             add_key(row_frame, ch, ch, width=4)


     bottom = tk.Frame(kb, bg="#050507")
     bottom.pack(anchor="center")
     add_key(bottom, "SPACE", " ", width=10)
     add_key(bottom, "DEL", "BACKSPACE", width=6)
     add_key(bottom, "CLEAR", "CLEAR", width=7)
     add_key(bottom, "DONE", "DONE", width=7)


     try:
         entry.focus_set()
     except Exception:
         pass


 def _touch_key_press(self, key):
     var = self._touch_keyboard_var
     entry = self._touch_keyboard_entry
     if var is None:
         return


     current = var.get()


     if key == "DONE":
         self._update_event_title()
         self._hide_touch_keyboard()
         return
     if key == "CLEAR":
         var.set("")
         self._update_event_title()
         return
     if key == "BACKSPACE":
         var.set(current[:-1])
         self._update_event_title()
         return


     # Keep the footer readable. Long titles still shrink to fit, but this
     # prevents someone from entering an entire sentence by accident.
     if len(current) >= 26:
         return


     var.set(current + str(key))
     self._update_event_title()
     try:
         entry.icursor(tk.END)
         entry.focus_set()
     except Exception:
         pass


 def _hide_touch_keyboard(self):
     if self._touch_keyboard is not None:
         try:
             self._touch_keyboard.destroy()
         except Exception:
             pass
     self._touch_keyboard = None
     self._touch_keyboard_entry = None
     self._touch_keyboard_var = None


 def _close_customize_overlay(self):
     # Save typed event title before destroying the Entry widget. This matters
     # on touch screens because focus/key events are not always fired reliably.
     try:
         self._update_event_title()
     except Exception:
         pass
     self._hide_touch_keyboard()
     if self._customize_overlay is not None:
         self._customize_overlay.destroy()
         self._customize_overlay = None
         self._timer_btn_refs = {}
         self._light_btn_refs = {}
         self._filter_btn_refs = {}


 def _update_event_title(self):
     try:
         title = self._event_title_var.get().strip()
     except Exception:
         title = self._event_title
     self._event_title = title or getattr(hw, "DEFAULT_EVENT_TITLE", "PANORAMA EVENT")
     try:
         hw.set_output_filter(self._selected_filter_key, self._event_title)
     except Exception as e:
         print(f"[filters] could not update event title: {e}")


 def _select_output_filter(self, filter_key):
     self._selected_filter_key = getattr(hw, "normalize_filter_key", lambda x: x)(filter_key)
     self._update_event_title()
     for key, btn in getattr(self, "_filter_btn_refs", {}).items():
         btn.config(bg=ACCENT if key == self._selected_filter_key else SURFACE2,
                    fg="white" if key == self._selected_filter_key else TEXT)
     label = {
         "none": "Regular",
         "sketch": "Sketch",
         "comic": "Comic",
         "pixel": "Retro",
         "branding": "Event Title",
     }.get(self._selected_filter_key, "Regular")
     print(f"[filters] UI selected {label}")


 def _apply_selected_filter_to_output(self, image_path):
     """Return the final file that should be previewed, printed, and uploaded."""
     try:
         self._update_event_title()
     except Exception:
         pass
     try:
         return hw.apply_output_filter_to_file(
             image_path,
             filter_key=self._selected_filter_key,
             event_title=self._event_title,
         )
     except Exception as e:
         print(f"[filters] output filter failed in UI: {e}")
         return image_path


 def _select_panorama_angle(self, degrees):
     import numpy as np
     n = len(hw.ANGLES_TO_CAPTURE)
     half = degrees / 2
     start = int(135 + half)  # e.g. 270 -> 270, 180 -> 225, 90 -> 180
     end   = int(135 - half)  # e.g. 270 -> 0,   180 -> 45,  90 -> 90
     # Clamp to servo limits
     start = min(start, 265)
     end   = max(end, 13)
     hw.ANGLES_TO_CAPTURE[:] = [int(round(x)) for x in
         list(reversed(list(np.linspace(end, start, n))))]
     print(f'[angle] Sweep set to {degrees}deg: {hw.ANGLES_TO_CAPTURE}')


 def _select_countdown(self, secs):
     self.countdown_secs = secs
     for s, btn in getattr(self, "_timer_btn_refs", {}).items():
         btn.config(bg=ACCENT if s == secs else SURFACE2,
                    fg="white" if s == secs else TEXT)


 def _select_light_mode(self, mode):
     self.light_mode = mode
     self.lights.set_mode(mode)
     for m, btn in getattr(self, "_light_btn_refs", {}).items():
         btn.config(bg=ACCENT if m == mode else SURFACE2,
                    fg="white" if m == mode else TEXT)


 def _on_brightness_change(self, value):
     try:
         self.lights.set_brightness(int(value))
     except (TypeError, ValueError):
         pass
 def _set_exposure_mode(self, auto):
     """Switch between auto and manual exposure. Updates hw flag and button colours."""
     self._auto_exposure  = auto
     hw.USE_AUTO_EXPOSURE = auto
     if hasattr(self, "_btn_exp_auto"):
         try:
             self._btn_exp_auto.config(
                 bg=ACCENT if auto else SURFACE2,
                 fg="white" if auto else TEXT,
             )
             self._btn_exp_manual.config(
                 bg=ACCENT if not auto else SURFACE2,
                 fg="white" if not auto else TEXT,
             )
         except Exception:
             pass
     print(f"[exposure] {'AUTO' if auto else 'MANUAL'}")



 def _set_auto_flash_mode(self, enabled):
     """Toggle auto flash from the customize screen."""
     self._auto_flash_enabled = bool(enabled)
     try:
         hw.AUTO_FLASH_ENABLED = self._auto_flash_enabled
     except Exception:
         pass

     if hasattr(self, "_btn_flash_on"):
         self._btn_flash_on.config(
             bg=ACCENT if self._auto_flash_enabled else SURFACE2,
             fg="white" if self._auto_flash_enabled else TEXT,
         )
     if hasattr(self, "_btn_flash_off"):
         self._btn_flash_off.config(
             bg=ACCENT if not self._auto_flash_enabled else SURFACE2,
             fg="white" if not self._auto_flash_enabled else TEXT,
         )


 def _prepare_auto_flash(self):
     """
     Turn the ring light on only if the camera sees a dark scene.

     Returns True if auto flash turned the light on, False otherwise.
     """
     try:
         if not self._auto_flash_enabled:
             return False
         if not getattr(hw, "AUTO_FLASH_ENABLED", True):
             return False

         is_dark, brightness = hw.scene_is_dark()
         if not is_dark:
             print(f"[auto flash] not needed, brightness={brightness:.1f}")
             return False

         print(f"[auto flash] dark scene detected, brightness={brightness:.1f}")
         self.lights.set_mode(self.light_mode)
         self.lights.set_brightness(getattr(hw, "AUTO_FLASH_BRIGHTNESS", 220))
         time.sleep(getattr(hw, "AUTO_FLASH_SETTLE_S", 0.45))
         return True

     except Exception as e:
         print(f"[auto flash] failed: {e}")
         return False


 def _restore_auto_flash(self, auto_flash_was_on):
     """Turn the ring back off after auto flash capture."""
     if not auto_flash_was_on:
         return
     try:
         self.lights.set_brightness(0)
     except Exception as e:
         print(f"[auto flash] restore failed: {e}")


 def _remember_last_print(self, image_path, qr_path=None):
     """
     Save the exact image and optional QR that was sent to the printer.
     Used by REPRINT LAST when receipt paper runs out halfway.
     """
     if image_path and os.path.exists(image_path):
         self._last_print_path = image_path
         self._last_print_qr_path = qr_path if qr_path and os.path.exists(qr_path) else None
         self._last_print_had_qr = self._last_print_qr_path is not None
         print(f"[reprint] saved last print: {self._last_print_path}")


 # Ã¢â€â‚¬Ã¢â€â‚¬ Print picker overlay Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬
 def _show_print_picker(self, image_path):
     self.pending_copies = hw.DEFAULT_PRINTS
     self._pending_output = image_path
     overlay = tk.Frame(self, bg=BG)
     overlay.place(x=0, y=0, width=W, height=H)
     self._picker_overlay = overlay
     tk.Label(overlay, text="PANORAMA READY",
              font=self.f_mono_lg, bg=BG, fg=ACCENT2).pack(pady=(14, 6))
     thumb_frame = tk.Frame(overlay, bg=BG)
     thumb_frame.pack(pady=(0, 8))
     try:
         pil = Image.open(image_path)
         pil.thumbnail((W - 80, 200), Image.LANCZOS)
         self._picker_thumb_ref = ImageTk.PhotoImage(pil)
         tk.Label(thumb_frame, image=self._picker_thumb_ref,
                  bg=BG, bd=1, relief="solid",
                  highlightbackground=BORDER).pack()
     except Exception:
         tk.Label(thumb_frame, text="(panorama preview unavailable)",
                  bg=BG, fg=MUTED, font=self.f_mono_sm).pack()
     tk.Label(overlay, text="How many copies?",
              font=self.f_sans_md, bg=BG, fg=TEXT).pack(pady=(8, 2))
     qty_row = tk.Frame(overlay, bg=BG)
     qty_row.pack(pady=(2, 8))
     self.btn_qty_minus = tk.Button(
         qty_row, text="-", font=("Courier", 22, "bold"),
         bg=SURFACE2, fg=TEXT, activebackground=BORDER, activeforeground=TEXT,
         relief="flat", cursor="hand2", width=3,
         command=self._on_picker_minus,
     )
     self.btn_qty_minus.pack(side="left", padx=8)
     self.lbl_qty = tk.Label(qty_row, text=str(self.pending_copies),
                             font=("Courier", 28, "bold"), bg=BG, fg=TEXT, width=3)
     self.lbl_qty.pack(side="left", padx=8)
     self.btn_qty_plus = tk.Button(
         qty_row, text="+", font=("Courier", 22, "bold"),
         bg=SURFACE2, fg=TEXT, activebackground=BORDER, activeforeground=TEXT,
         relief="flat", cursor="hand2", width=3,
         command=self._on_picker_plus,
     )
     self.btn_qty_plus.pack(side="left", padx=8)
     self._refresh_picker_qty_buttons()
     act_row = tk.Frame(overlay, bg=BG)
     act_row.pack(pady=(10, 0))
     # REDO is only here, not on the sidebar
     tk.Button(
         act_row, text="REDO",
         font=self.f_mono_md, bg=SURFACE2, fg=TEXT,
         activebackground=BORDER, activeforeground=TEXT,
         relief="flat", cursor="hand2", pady=10, padx=20,
         command=self._on_picker_redo,
     ).pack(side="left", padx=8)
     tk.Button(
         act_row, text="QR + PRINT",
         font=self.f_mono_md, bg=ACCENT, fg="white",
         activebackground=ACCENT2, activeforeground="white",
         relief="flat", cursor="hand2", pady=10, padx=12,
         command=self._on_qr_and_print,
     ).pack(side="left", padx=4)
     tk.Button(
         act_row, text="QR SCREEN",
         font=self.f_mono_md, bg=SURFACE2, fg=TEXT,
         activebackground=BORDER, activeforeground=TEXT,
         relief="flat", cursor="hand2", pady=10, padx=12,
         command=self._on_qr_screen_only,
     ).pack(side="left", padx=4)
     tk.Button(
         act_row, text="PRINT",
         font=self.f_mono_md, bg=SURFACE2, fg=TEXT,
         activebackground=BORDER, activeforeground=TEXT,
         relief="flat", cursor="hand2", pady=10, padx=12,
         command=self._on_print_confirmed,
     ).pack(side="left", padx=4)
     tk.Button(
         act_row, text="REPRINT LAST",
         font=self.f_mono_md, bg=SURFACE2, fg=TEXT,
         activebackground=BORDER, activeforeground=TEXT,
         relief="flat", cursor="hand2", pady=10, padx=12,
         command=self._on_reprint_last,
     ).pack(side="left", padx=4)
     tk.Button(
         act_row, text="SKIP",
         font=self.f_mono_md, bg=SURFACE2, fg=MUTED,
         activebackground=BORDER, activeforeground=TEXT,
         relief="flat", cursor="hand2", pady=10, padx=24,
         command=self._on_print_skip,
     ).pack(side="left", padx=8)
 def _close_print_picker(self):
     if self._picker_overlay is not None:
         self._picker_overlay.destroy()
         self._picker_overlay = None
         self._picker_thumb_ref = None
 def _refresh_picker_qty_buttons(self):
     if not hasattr(self, "btn_qty_plus"):
         return
     self.btn_qty_plus.config(
         state="disabled" if self.pending_copies >= hw.MAX_PRINTS_PER_RUN else "normal")
     self.btn_qty_minus.config(
         state="disabled" if self.pending_copies <= 1 else "normal")
 def _on_picker_plus(self):
     if self.pending_copies < hw.MAX_PRINTS_PER_RUN:
         self.pending_copies += 1
         self.lbl_qty.config(text=str(self.pending_copies))
         self._refresh_picker_qty_buttons()
 def _on_picker_minus(self):
     if self.pending_copies > 1:
         self.pending_copies -= 1
         self.lbl_qty.config(text=str(self.pending_copies))
         self._refresh_picker_qty_buttons()


 def _on_picker_redo(self):
     """Close picker and start a fresh capture in whichever mode is active."""
     self._close_print_picker()
     if not self._is_running:
         if self._capture_mode == "panorama":
             target = self._run_booth
         elif self._capture_mode == "still":
             target = self._run_still
         else:
             target = self._run_multi
         threading.Thread(target=target, daemon=True).start()


 def _on_qr_screen_only(self):
     """Upload and show QR on screen, but print normally without QR."""
     self._close_print_picker()
     threading.Thread(target=self._run_qr_screen_only, daemon=True).start()


 def _run_qr_screen_only(self):
     self._is_running = True


     def ui(fn, *a, **kw):
         self.after(0, lambda: fn(*a, **kw))


     try:
         image_path = getattr(self, "_pending_output", hw.STITCHED_OUTPUT)
         ui(self.set_phase, "printing", "Uploading to Google Drive...")


         import duen_qr
         result = duen_qr.upload_and_generate_qr(image_path)


         if result["error"]:
             ui(self.set_phase, "error", f"Upload failed: {result['error']}")
             return


         ui(self._show_qr_overlay, result["qr_path"])


         copies = self.pending_copies
         for i in range(copies):
             ui(self.set_phase, "printing", f"Printing {i+1} / {copies}...")
             self._remember_last_print(image_path)
             hw.process_and_print(image_path)
             self.print_session += 1
             self.print_total   += 1
             hw.save_print_total(self.print_total)
             ui(self._refresh_counters)
             if i < copies - 1:
                 time.sleep(3)


         ui(self.set_last_event,
            f"Last print: {datetime.now().strftime('%H:%M:%S')}")


     except Exception as e:
         ui(self.set_phase, "error", f"QR screen failed: {e}")
     finally:
         self._is_running = False


 def _on_qr_and_print(self):
      """User tapped QR + PRINT ? upload, generate QR, show on screen, print with QR."""
      self._close_print_picker()
      threading.Thread(target=self._run_qr_and_print, daemon=True).start()


 def _run_qr_and_print(self):
     """Background thread: uploads photo, generates QR, prints strip with QR."""
     self._is_running = True


     def ui(fn, *a, **kw):
         self.after(0, lambda: fn(*a, **kw))


     try:
         image_path = getattr(self, "_pending_output", hw.STITCHED_OUTPUT)


         # Tell user what is happening
         ui(self.set_phase, "printing", "Uploading to Google Drive...")


         import duen_qr
         result = duen_qr.upload_and_generate_qr(image_path)


         if result["error"]:
             ui(self.set_phase, "error", f"Upload failed: {result['error']}")
             return


         # Show QR code on screen so user can scan it
         ui(self._show_qr_overlay, result["qr_path"])


         # Print the strip with QR code at the bottom
         copies = self.pending_copies
         for i in range(copies):
             ui(self.set_phase, "printing", f"Printing {i+1} / {copies}...")
             self._remember_last_print(image_path, qr_path=result["qr_path"])
             hw.process_and_print(image_path, qr_path=result["qr_path"])
             self.print_session += 1
             self.print_total   += 1
             hw.save_print_total(self.print_total)
             ui(self._refresh_counters)
             if i < copies - 1:
                 time.sleep(3)
         ui(self.set_last_event,
            f"Last print: {datetime.now().strftime('%H:%M:%S')}")


     except Exception as e:
         ui(self.set_phase, "error", f"QR print failed: {e}")
     finally:
         self._is_running = False


 def _show_qr_overlay(self, qr_path):
     """Show the QR code fullscreen so the user can scan it with their phone."""
     overlay = tk.Frame(self, bg=BG)
     overlay.place(x=0, y=0, width=W, height=H)
     self._qr_overlay = overlay


     tk.Button(
         overlay, text="X",
         font=self.f_mono_lg, bg=BG, fg=MUTED,
         activebackground=BG, activeforeground=TEXT,
         relief="flat", cursor="hand2", padx=8, pady=4,
         command=self._close_qr_overlay,
     ).place(x=W-60, y=8)
     tk.Label(overlay, text="Scan to view your photo",
              font=self.f_mono_lg, bg=BG, fg=ACCENT2).pack(pady=(20, 8))


     try:
         qr_img = Image.open(qr_path)
         qr_img.thumbnail((340, 340), Image.LANCZOS)
         self._qr_img_ref = ImageTk.PhotoImage(qr_img)
         tk.Label(overlay, image=self._qr_img_ref, bg=BG).pack(pady=(0, 12))
     except Exception:
         tk.Label(overlay, text="(QR code unavailable)",
                  bg=BG, fg=MUTED, font=self.f_mono_sm).pack()


     tk.Label(overlay, text="Photo deletes automatically after 48 hours",
              font=self.f_mono_sm, bg=BG, fg=MUTED).pack(pady=(0, 12))


     tk.Button(
         overlay, text="DONE",
         font=self.f_mono_md, bg=SURFACE2, fg=TEXT,
         activebackground=BORDER, activeforeground=TEXT,
         relief="flat", cursor="hand2", pady=10, padx=30,
         command=self._close_qr_overlay,
     ).pack()


 def _close_qr_overlay(self):
     """Close the QR overlay and return to idle."""
     if hasattr(self, "_qr_overlay") and self._qr_overlay:
         self._qr_overlay.destroy()
         self._qr_overlay = None
     self._latest_still_pil = None
     self.set_phase("idle", "Ready. Press the button.")
     self.set_feed_label("Live feed")



 def _on_reprint_last(self):
     """Reprint the last saved output without retaking photos."""
     if self._is_running:
         return

     image_path = self._last_print_path or getattr(self, "_pending_output", None)
     if not image_path or not os.path.exists(image_path):
         self.set_phase("error", "No previous print found to reprint.")
         return

     self._close_print_picker()
     threading.Thread(
         target=self._run_reprint_last,
         args=(image_path, self._last_print_qr_path),
         daemon=True,
     ).start()


 def _run_reprint_last(self, image_path, qr_path=None):
     self._is_running = True

     def ui(fn, *a, **kw):
         self.after(0, lambda: fn(*a, **kw))

     try:
         ui(self.set_phase, "printing", "Reprinting last receipt...")

         if qr_path and os.path.exists(qr_path):
             hw.process_and_print(image_path, qr_path=qr_path)
         else:
             hw.process_and_print(image_path)

         self.print_session += 1
         self.print_total += 1
         hw.save_print_total(self.print_total)
         ui(self._refresh_counters)

         ui(self.set_phase, "done", "Reprint complete.")
         ui(self.set_last_event, f"Reprinted: {datetime.now().strftime('%H:%M:%S')}")

     except Exception as e:
         ui(self.set_phase, "error", f"Reprint failed: {e}")

     finally:
         self._is_running = False


 def _on_print_confirmed(self):
     self._close_print_picker()
     threading.Thread(target=self._run_print_only, daemon=True).start()


 def _on_print_skip(self):
    self._close_print_picker()
    self._latest_still_pil = None
    self.set_phase("idle", "Ready. Press START.")
    self.set_feed_label("Live feed")


 def _run_print_only(self):
     self._is_running = True


     def ui(fn, *a, **kw):
         self.after(0, lambda: fn(*a, **kw))


     copies = self.pending_copies
     try:
         for i in range(copies):
            ui(self.set_phase, "printing", f"Printing {i+1} / {copies}...")
            image_path = getattr(self, "_pending_output", hw.STITCHED_OUTPUT)
            self._remember_last_print(image_path)
            hw.process_and_print(image_path)
            self.print_session += 1
            self.print_total   += 1
            hw.save_print_total(self.print_total)
            ui(self._refresh_counters)
            if i < copies - 1:
                time.sleep(3)   # give the printer time to finish and release USB before next job
         ui(self.set_phase, "idle", f"Printed {copies} cop{'y' if copies == 1 else 'ies'}. Ready!")
         ui(self.set_last_event, f"Last print: {datetime.now().strftime('%H:%M:%S')}")
         self._latest_still_pil = None
         ui(self.set_feed_label, "Live feed")


     except Exception as e:
         ui(self.set_phase, "error", f"Print failed: {e}")
     finally:
         self._is_running = False


 # Ã¢â€â‚¬Ã¢â€â‚¬ Quit / cleanup Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬


 # ── Stitching loading overlay ─────────────────────────────────────────────
 # Shown while OpenCV stitching runs. The ring enters dim warm snake mode as
 # a physical cue that the booth is working. Previous mode is restored on hide.


 def _show_loading_overlay(self):
     """Show the stitching loading card and start ring snake animation."""
     if self._loading_overlay is not None:
         return


     # Save current light mode so we can restore it afterward.
     self._light_mode_before_load = self.light_mode


     # Switch ring to snake at dim warm brightness.
     # Snake runs in LightController's own background thread —
     # no need to drive pixel-by-pixel from the UI thread.
     self.lights.set_brightness(30)
     self.lights.set_mode("snake")


     overlay = tk.Frame(self, bg=BG)
     overlay.place(x=0, y=0, width=W, height=H)
     overlay.lift()
     self._loading_overlay = overlay


     card = tk.Frame(
         overlay,
         bg=SURFACE,
         bd=0,
         highlightthickness=1,
         highlightbackground=BORDER,
     )
     card.place(relx=0.5, rely=0.5, anchor="center", width=430, height=230)


     tk.Label(
         card,
         text="Stitching Images...",
         font=("DejaVu Sans", 24, "bold"),
         bg=SURFACE,
         fg=TEXT,
     ).pack(pady=(38, 8))


     tk.Label(
         card,
         text="Please wait while your panorama is created",
         font=self.f_sans_sm,
         bg=SURFACE,
         fg=MUTED,
     ).pack(pady=(0, 12))


     tk.Label(
         card,
         text="Do not move the camera",
         font=self.f_mono_sm,
         bg=SURFACE,
         fg=FAINT,
     ).pack(pady=(0, 8))


     # Pulsing dots give an on-screen visual while the ring animates physically.
     dot_row = tk.Frame(card, bg=SURFACE)
     dot_row.pack(pady=(0, 18))
     self._loading_dots = []
     for _ in range(5):
         d = tk.Frame(dot_row, bg=FAINT, width=10, height=10)
         d.pack(side="left", padx=4)
         self._loading_dots.append(d)


     self._loading_animating  = True
     self._loading_dot_index  = 0
     self._animate_loading_dots()


 def _animate_loading_dots(self):
     """Pulse the on-screen progress dots while stitching runs."""
     if not self._loading_animating:
         return
     dots = getattr(self, "_loading_dots", [])
     for i, d in enumerate(dots):
         d.config(bg=ACCENT2 if i == self._loading_dot_index else FAINT)
     self._loading_dot_index = (self._loading_dot_index + 1) % max(1, len(dots))
     self.after(250, self._animate_loading_dots)


 def _hide_loading_overlay(self):
     """Remove the loading card and restore the ring to its previous state."""
     self._loading_animating = False
     if self._loading_overlay is not None:
         self._loading_overlay.destroy()
         self._loading_overlay = None


     # Restore the light mode and brightness that were active before loading.
     try:
         if self._light_mode_before_load is not None:
             self.lights.set_mode(self._light_mode_before_load)
         self.lights.set_brightness(0)
     except Exception:
         pass


 def _quit(self):
     hw.save_print_total(self.print_total)
     self._preview_running = False
     self._preview_active  = False
     self._hide_loading_overlay()
     self._close_qr_overlay()
     for cleanup in (self.lights.stop, hw.cleanup_hardware, self.destroy):
         try:
             cleanup()
         except Exception:
             pass


 # Ã¢â€â‚¬Ã¢â€â‚¬ Main booth flow (background thread) Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬
 def _run_booth(self):
     if self._is_running:
         return
     self._stop_requested = False
     self._is_running = True
     auto_flash_was_on = False


     def ui(fn, *a, **kw):
         self.after(0, lambda: fn(*a, **kw))


     def progress(event, **data):
         if event == "warmup":
             ui(self.set_phase, "capturing", data.get("message", "Warming up camera exposure..."), 0)
         elif event == "move_first":
             angle = data.get("angle")
             ui(self.set_phase, "capturing", data.get("message", "Moving to first angle..."), 0, angle)
         elif event == "flush_first":
             angle = data.get("angle")
             ui(self.set_phase, "capturing", data.get("message", "Flushing first frame..."), 0, angle)
         elif event == "capture":
             idx = data.get("index", 0)
             angle = data.get("angle")
             total = data.get("total", TOTAL_ANGLES)
             ui(self.set_phase, "capturing", f"Capturing angle {angle} ({idx + 1}/{total})", idx, angle)
         elif event == "stitch":
             ui(self.set_phase, "stitching", data.get("message", "Stitching panorama..."))
             ui(self._show_loading_overlay)
         elif event == "error":
             ui(self._hide_loading_overlay)
             ui(self.set_phase, "error", data.get("message", "Stitching failed."))


     def show_capture(filepath, index, angle):
         try:
             ui(self._show_image, Image.open(filepath).copy())
             ui(self.set_feed_label, f"Shot {index + 1} . {angle}")
         except Exception:
             pass


     try:
         ui(self._close_customize_overlay)
         self._latest_still_pil = None


         # Countdown only affects the user experience. The hardware capture path below
         # is the same Perfect Stitch sequence that works standalone.
         if self.countdown_secs > 0:
             for n in range(self.countdown_secs, 0, -1):
                 if self._stop_requested:
                     return
                 ui(self.set_phase, "capturing", f"Get ready... {n}", 0)
                 self._countdown_value = n
                 self.lights.flash_once(brightness_fraction=0.30, duration_s=0.15)
                 time.sleep(max(0.0, 1.0 - 0.15))
             self._countdown_value = None


         self._preview_active = False
         # Let any in-progress preview capture finish before the Perfect Stitch still path starts.
         time.sleep(max(0.25, 2.0 / hw.PREVIEW_FPS_HZ))


         if self._stop_requested:
             return
         ui(self.set_phase, "capturing", "Starting Perfect Stitch capture...", 0)


         # For panorama, use steady auto flash for the full sweep.
         # Do not flash each frame, because changing light between frames can hurt stitching.
         auto_flash_was_on = self._prepare_auto_flash()

         output = hw.capture_and_stitch_once(
             progress_callback=progress,
             image_callback=show_capture,
             stop_flag=lambda: self._stop_requested,
         )


         self._restore_auto_flash(auto_flash_was_on)

         if output and os.path.exists(output) and not self._stop_requested:
             ui(self.set_phase, "stitching", "Applying selected filter...")
             output = self._apply_selected_filter_to_output(output)


         if self._stop_requested:
            ui(self._hide_loading_overlay)
            return


         if output and os.path.exists(output):
            ui(self._hide_loading_overlay)
            try:
                ui(self._show_image, Image.open(output).copy())
                ui(self.set_feed_label, "Stitched panorama")
            except Exception:
                pass
            ui(self.set_phase, "done", "Choose how many copies to print.")
            ui(self._show_print_picker, output)
         else:
            ui(self._hide_loading_overlay)
            ui(self.set_phase, "error", "Stitching failed. Check image overlap, lighting, and the saved raw photos.")


     except Exception as e:
         ui(self._hide_loading_overlay)
         ui(self.set_phase, "error", f"Error: {e}")
     finally:
         try:
             self._restore_auto_flash(auto_flash_was_on)
         except Exception:
             pass
         self._preview_active  = True
         self._countdown_value = None
         self._is_running      = False
         print("--- Ready for next shot ---")


# Ã¢â€â‚¬Ã¢â€â‚¬ ENTRY POINT Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬Ã¢â€â‚¬
if __name__ == "__main__":
 app = BoothUI()
 try:
     app.mainloop()
 finally:
     for cleanup in (app.lights.stop, hw.cleanup_hardware):
         try:
             cleanup()
         except Exception:
             pass
