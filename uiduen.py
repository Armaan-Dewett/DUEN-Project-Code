"""
Touchscreen UI for Perfect Stitch.

Launches the existing panorama script as a subprocess so the stitch
code itself is never touched. Run manually:

    python3 ui.py

Place this file in the same directory as the panorama script. If your
panorama script has a different filename, change STITCH_SCRIPT below.
"""

import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from tkinter import messagebox


STITCH_SCRIPT = "perfect_stitch.py"

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
STITCH_SCRIPT_PATH = os.path.join(SCRIPT_DIR, STITCH_SCRIPT)


BG = "#111111"
FG = "#f0f0f0"
MUTED = "#888888"
ACCENT = "#2ecc71"
ACCENT_DIM = "#444444"
MODE_ON = "#1f6feb"
MODE_OFF = "#222222"
DISABLED = "#1a1a1a"


class PerfectStitchUI:
    def __init__(self, root):
        self.root = root
        self.root.title("Perfect Stitch")
        self.root.configure(bg=BG)
        self.root.attributes("-fullscreen", True)
        self.root.bind("<Escape>", lambda _e: self.root.attributes("-fullscreen", False))

        self.mode = tk.StringVar(value="panoramic")
        self.proc = None
        self.output_queue = queue.Queue()
        self.reader_thread = None

        self._build_ui()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)
        self.root.after(100, self._drain_output)

    def _build_ui(self):
        outer = tk.Frame(self.root, bg=BG, padx=24, pady=24)
        outer.pack(fill="both", expand=True)

        title = tk.Label(
            outer,
            text="Perfect Stitch",
            font=("Helvetica", 28, "bold"),
            bg=BG,
            fg=FG,
        )
        title.pack(anchor="w", pady=(0, 16))

        mode_frame = tk.Frame(outer, bg=BG)
        mode_frame.pack(fill="x", pady=(0, 16))

        self.pano_btn = tk.Button(
            mode_frame,
            text="Panoramic",
            font=("Helvetica", 22, "bold"),
            height=2,
            command=lambda: self._set_mode("panoramic"),
            relief="flat",
            bd=0,
            activeforeground=FG,
        )
        self.pano_btn.pack(side="left", fill="x", expand=True, padx=(0, 8), ipady=10)

        self.still_btn = tk.Button(
            mode_frame,
            text="Still (coming soon)",
            font=("Helvetica", 22, "bold"),
            height=2,
            state="disabled",
            relief="flat",
            bd=0,
            disabledforeground=MUTED,
            bg=DISABLED,
        )
        self.still_btn.pack(side="left", fill="x", expand=True, padx=(8, 0), ipady=10)

        self.start_btn = tk.Button(
            outer,
            text="START",
            font=("Helvetica", 36, "bold"),
            height=2,
            command=self._on_start,
            relief="flat",
            bd=0,
            bg=ACCENT,
            fg="#000000",
            activebackground=ACCENT,
            activeforeground="#000000",
        )
        self.start_btn.pack(fill="x", pady=(0, 16), ipady=20)

        status_frame = tk.Frame(outer, bg=BG)
        status_frame.pack(fill="both", expand=True)

        self.status_text = tk.Text(
            status_frame,
            bg="#000000",
            fg=FG,
            insertbackground=FG,
            font=("Menlo", 13),
            wrap="word",
            state="disabled",
            bd=0,
            highlightthickness=0,
        )
        self.status_text.pack(side="left", fill="both", expand=True)

        scrollbar = tk.Scrollbar(status_frame, command=self.status_text.yview)
        scrollbar.pack(side="right", fill="y")
        self.status_text.config(yscrollcommand=scrollbar.set)

        self.footer = tk.Label(
            outer,
            text="Idle",
            font=("Helvetica", 16),
            bg=BG,
            fg=MUTED,
            anchor="w",
        )
        self.footer.pack(fill="x", pady=(12, 0))

        self._refresh_mode_buttons()

    def _set_mode(self, mode):
        if mode == "still":
            return
        self.mode.set(mode)
        self._refresh_mode_buttons()

    def _refresh_mode_buttons(self):
        if self.mode.get() == "panoramic":
            self.pano_btn.config(bg=MODE_ON, fg=FG, activebackground=MODE_ON)
        else:
            self.pano_btn.config(bg=MODE_OFF, fg=FG, activebackground=MODE_OFF)

    def _on_start(self):
        if self.proc is not None and self.proc.poll() is None:
            return

        if not os.path.exists(STITCH_SCRIPT_PATH):
            messagebox.showerror(
                "Script not found",
                f"Could not find:\n{STITCH_SCRIPT_PATH}\n\n"
                f"Edit STITCH_SCRIPT in ui.py if your panorama script has a different name.",
            )
            return

        self._clear_status()
        self._append_status(f"Launching: {STITCH_SCRIPT_PATH}\n")
        self.start_btn.config(state="disabled", text="RUNNING…", bg=ACCENT_DIM, fg=MUTED)
        self.footer.config(text="Running", fg=FG)

        try:
            self.proc = subprocess.Popen(
                [sys.executable, "-u", STITCH_SCRIPT_PATH],
                cwd=SCRIPT_DIR,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                bufsize=1,
                text=True,
            )
        except Exception as e:
            self._append_status(f"Failed to start: {e}\n")
            self._mark_done(error=str(e))
            return

        self.reader_thread = threading.Thread(
            target=self._read_subprocess_output,
            args=(self.proc,),
            daemon=True,
        )
        self.reader_thread.start()

    def _read_subprocess_output(self, proc):
        try:
            for line in iter(proc.stdout.readline, ""):
                if not line:
                    break
                self.output_queue.put(line)
        finally:
            try:
                proc.stdout.close()
            except Exception:
                pass

    def _drain_output(self):
        try:
            while True:
                line = self.output_queue.get_nowait()
                self._append_status(line)
        except queue.Empty:
            pass

        if self.proc is not None and self.proc.poll() is not None:
            if self.reader_thread is not None and not self.reader_thread.is_alive():
                exit_code = self.proc.returncode
                if exit_code == 0:
                    self._mark_done()
                else:
                    self._mark_done(error=f"exit {exit_code}")
                self.proc = None
                self.reader_thread = None

        self.root.after(100, self._drain_output)

    def _append_status(self, text):
        self.status_text.config(state="normal")
        self.status_text.insert("end", text)
        self.status_text.see("end")
        self.status_text.config(state="disabled")

    def _clear_status(self):
        self.status_text.config(state="normal")
        self.status_text.delete("1.0", "end")
        self.status_text.config(state="disabled")

    def _mark_done(self, error=None):
        self.start_btn.config(state="normal", text="START", bg=ACCENT, fg="#000000")
        if error:
            self.footer.config(text=f"Error: {error}", fg="#e74c3c")
        else:
            self.footer.config(text="Done", fg=ACCENT)

    def _on_close(self):
        if self.proc is not None and self.proc.poll() is None:
            confirm = messagebox.askyesno(
                "Stop run?",
                "A panorama run is in progress. Stop it and close?",
            )
            if not confirm:
                return
            try:
                self.proc.terminate()
                try:
                    self.proc.wait(timeout=3)
                except subprocess.TimeoutExpired:
                    self.proc.kill()
            except Exception:
                pass
        self.root.destroy()


def main():
    root = tk.Tk()
    PerfectStitchUI(root)
    root.mainloop()


if __name__ == "__main__":
    main()
