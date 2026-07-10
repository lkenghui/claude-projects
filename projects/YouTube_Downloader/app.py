from __future__ import annotations

import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import threading
import subprocess
import sys
import os
import json
import shutil
import re
import yt_dlp


# ── Helpers ──────────────────────────────────────────────────────────────────

def ffmpeg_path():
    """Return path to ffmpeg, preferring bundled copy."""
    bundled = os.path.join(getattr(sys, "_MEIPASS", os.path.dirname(__file__)), "ffmpeg")
    return bundled if os.path.isfile(bundled) else shutil.which("ffmpeg") or "ffmpeg"


def ffprobe_path():
    bundled = os.path.join(getattr(sys, "_MEIPASS", os.path.dirname(__file__)), "ffprobe")
    return bundled if os.path.isfile(bundled) else shutil.which("ffprobe") or "ffprobe"


def sanitize(name: str) -> str:
    return re.sub(r'[\\/*?:"<>|]', "_", name)


# ── Main App ─────────────────────────────────────────────────────────────────

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("YouTube Downloader")
        self.resizable(False, False)
        self.configure(bg="#1e1e2e")

        self._output_dir = os.path.expanduser("~/Downloads")
        self._cancel = threading.Event()
        self._thread = None

        self._build_ui()

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        PAD = 16
        BG = "#1e1e2e"
        CARD = "#2a2a3e"
        ACCENT = "#7c6af7"
        FG = "#cdd6f4"
        MUTED = "#6c7086"
        ENTRY_BG = "#313244"

        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("TProgressbar", troughcolor=ENTRY_BG, background=ACCENT, thickness=8)
        style.configure("TCombobox", fieldbackground=ENTRY_BG, background=ENTRY_BG,
                        foreground=FG, arrowcolor=FG, selectbackground=ENTRY_BG,
                        selectforeground=FG)
        style.map("TCombobox", fieldbackground=[("readonly", ENTRY_BG)])

        outer = tk.Frame(self, bg=BG, padx=PAD, pady=PAD)
        outer.pack()

        # ── Title
        tk.Label(outer, text="YouTube Downloader", font=("SF Pro Display", 20, "bold"),
                 bg=BG, fg=FG).pack(anchor="w", pady=(0, 4))
        tk.Label(outer, text="Download & compress YouTube videos at full fidelity",
                 font=("SF Pro Text", 11), bg=BG, fg=MUTED).pack(anchor="w", pady=(0, PAD))

        # ── URL card
        self._card(outer, BG, CARD, FG, MUTED, ENTRY_BG, ACCENT, PAD)

        # ── Log
        log_frame = tk.Frame(outer, bg=CARD, bd=0)
        log_frame.pack(fill="x", pady=(PAD, 0))

        tk.Label(log_frame, text="Log", font=("SF Pro Text", 11, "bold"),
                 bg=CARD, fg=FG, padx=12, pady=8).pack(anchor="w")

        self._log = tk.Text(log_frame, height=10, width=68, bg="#181825", fg="#a6adc8",
                            font=("SF Mono", 10), relief="flat", state="disabled",
                            padx=10, pady=8, wrap="word")
        self._log.pack(padx=12, pady=(0, 12))

        # ── Progress
        prog_frame = tk.Frame(outer, bg=BG)
        prog_frame.pack(fill="x", pady=(PAD, 0))

        self._status_var = tk.StringVar(value="Ready")
        tk.Label(prog_frame, textvariable=self._status_var, font=("SF Pro Text", 10),
                 bg=BG, fg=MUTED).pack(anchor="w")
        self._progress = ttk.Progressbar(prog_frame, mode="determinate", length=600)
        self._progress.pack(fill="x", pady=(4, 0))

        # ── Buttons
        btn_frame = tk.Frame(outer, bg=BG)
        btn_frame.pack(fill="x", pady=(PAD, 0))

        self._dl_btn = tk.Button(
            btn_frame, text="Download", font=("SF Pro Text", 12, "bold"),
            bg=ACCENT, fg="white", relief="flat", padx=20, pady=8, cursor="hand2",
            command=self._start
        )
        self._dl_btn.pack(side="left")

        self._cancel_btn = tk.Button(
            btn_frame, text="Cancel", font=("SF Pro Text", 12),
            bg=ENTRY_BG, fg=FG, relief="flat", padx=20, pady=8, cursor="hand2",
            state="disabled", command=self._do_cancel
        )
        self._cancel_btn.pack(side="left", padx=(8, 0))

        self._folder_btn = tk.Button(
            btn_frame, text=f"📁  {self._output_dir}", font=("SF Pro Text", 10),
            bg=CARD, fg=MUTED, relief="flat", padx=12, pady=8, cursor="hand2",
            command=self._choose_folder
        )
        self._folder_btn.pack(side="right")

    def _card(self, parent, BG, CARD, FG, MUTED, ENTRY_BG, ACCENT, PAD):
        card = tk.Frame(parent, bg=CARD, pady=12, padx=12)
        card.pack(fill="x", pady=(0, PAD))

        # URL
        tk.Label(card, text="YouTube URL", font=("SF Pro Text", 11, "bold"),
                 bg=CARD, fg=FG).grid(row=0, column=0, sticky="w", pady=(0, 4))
        self._url_var = tk.StringVar()
        url_entry = tk.Entry(card, textvariable=self._url_var, font=("SF Pro Text", 12),
                             bg=ENTRY_BG, fg=FG, relief="flat", insertbackground=FG,
                             width=55)
        url_entry.grid(row=1, column=0, columnspan=3, sticky="ew", ipady=6, pady=(0, 12))

        # Quality
        tk.Label(card, text="Video Quality", font=("SF Pro Text", 11, "bold"),
                 bg=CARD, fg=FG).grid(row=2, column=0, sticky="w", pady=(0, 4))
        self._quality_var = tk.StringVar(value="Best (4K/HDR)")
        quality_cb = ttk.Combobox(card, textvariable=self._quality_var, state="readonly",
                                  values=["Best (4K/HDR)", "1080p", "720p", "480p", "Audio Only"],
                                  width=22, font=("SF Pro Text", 11))
        quality_cb.grid(row=3, column=0, sticky="w", pady=(0, 12))

        # Compress toggle
        tk.Label(card, text="Compress After Download", font=("SF Pro Text", 11, "bold"),
                 bg=CARD, fg=FG).grid(row=2, column=1, sticky="w", padx=(24, 0), pady=(0, 4))
        self._compress_var = tk.BooleanVar(value=False)
        compress_chk = tk.Checkbutton(card, variable=self._compress_var, bg=CARD,
                                      activebackground=CARD, command=self._toggle_compress,
                                      cursor="hand2")
        compress_chk.grid(row=3, column=1, sticky="w", padx=(24, 0), pady=(0, 12))

        # Compression preset
        tk.Label(card, text="Compression Preset", font=("SF Pro Text", 11, "bold"),
                 bg=CARD, fg=FG).grid(row=2, column=2, sticky="w", padx=(24, 0), pady=(0, 4))
        self._preset_var = tk.StringVar(value="High (visually lossless)")
        self._preset_cb = ttk.Combobox(card, textvariable=self._preset_var, state="disabled",
                                       values=["High (visually lossless)", "Medium", "Small"],
                                       width=22, font=("SF Pro Text", 11))
        self._preset_cb.grid(row=3, column=2, sticky="w", padx=(24, 0), pady=(0, 12))

        # Format
        tk.Label(card, text="Output Format", font=("SF Pro Text", 11, "bold"),
                 bg=CARD, fg=FG).grid(row=4, column=0, sticky="w", pady=(0, 4))
        self._fmt_var = tk.StringVar(value="MKV")
        fmt_cb = ttk.Combobox(card, textvariable=self._fmt_var, state="readonly",
                               values=["MKV", "MP4", "MP3 (audio)", "M4A (audio)"],
                               width=22, font=("SF Pro Text", 11))
        fmt_cb.grid(row=5, column=0, sticky="w")

    # ── Logic ─────────────────────────────────────────────────────────────────

    def _toggle_compress(self):
        state = "readonly" if self._compress_var.get() else "disabled"
        self._preset_cb.configure(state=state)

    def _choose_folder(self):
        folder = filedialog.askdirectory(initialdir=self._output_dir)
        if folder:
            self._output_dir = folder
            label = folder if len(folder) < 40 else "…" + folder[-37:]
            self._folder_btn.configure(text=f"📁  {label}")

    def _log_write(self, msg: str):
        self._log.configure(state="normal")
        self._log.insert("end", msg + "\n")
        self._log.see("end")
        self._log.configure(state="disabled")

    def _set_status(self, text: str, pct: float = None):
        self._status_var.set(text)
        if pct is not None:
            self._progress["value"] = pct

    def _set_buttons(self, running: bool):
        self._dl_btn.configure(state="disabled" if running else "normal")
        self._cancel_btn.configure(state="normal" if running else "disabled")

    def _do_cancel(self):
        self._cancel.set()
        self._log_write("⚠  Cancel requested…")

    def _start(self):
        url = self._url_var.get().strip()
        if not url:
            messagebox.showwarning("No URL", "Please enter a YouTube URL.")
            return
        if shutil.which("ffmpeg") is None and not os.path.isfile(ffmpeg_path()):
            messagebox.showerror(
                "FFmpeg not found",
                "FFmpeg is required but was not found.\n\nInstall it with:\n  brew install ffmpeg"
            )
            return

        self._cancel.clear()
        self._log.configure(state="normal")
        self._log.delete("1.0", "end")
        self._log.configure(state="disabled")
        self._set_buttons(True)
        self._set_status("Starting…", 0)

        self._thread = threading.Thread(target=self._run, args=(url,), daemon=True)
        self._thread.start()

    def _run(self, url: str):
        try:
            filepath = self._download(url)
            if filepath and self._compress_var.get() and not self._cancel.is_set():
                filepath = self._compress(filepath)
            if filepath and not self._cancel.is_set():
                self.after(0, lambda: self._set_status("Done!", 100))
                self.after(0, lambda: self._log_write(f"\n✅  Saved to: {filepath}"))
            elif self._cancel.is_set():
                self.after(0, lambda: self._set_status("Cancelled", 0))
        except Exception as e:
            self.after(0, lambda: self._log_write(f"\n❌  Error: {e}"))
            self.after(0, lambda: self._set_status("Error", 0))
        finally:
            self.after(0, lambda: self._set_buttons(False))

    # ── Download ──────────────────────────────────────────────────────────────

    def _download(self, url: str) -> str | None:
        quality = self._quality_var.get()
        fmt = self._fmt_var.get()

        # Build format selector
        if fmt in ("MP3 (audio)", "M4A (audio)") or quality == "Audio Only":
            fmt_selector = "bestaudio/best"
            ext = "mp3" if fmt == "MP3 (audio)" else "m4a"
            postprocessors = [{
                "key": "FFmpegExtractAudio",
                "preferredcodec": ext,
                "preferredquality": "0",
            }]
        else:
            ext = fmt.lower()
            postprocessors = [{"key": "FFmpegVideoConvertor", "preferedformat": ext}]
            if quality == "Best (4K/HDR)":
                fmt_selector = "bestvideo+bestaudio/best"
            elif quality == "1080p":
                fmt_selector = "bestvideo[height<=1080]+bestaudio/best[height<=1080]"
            elif quality == "720p":
                fmt_selector = "bestvideo[height<=720]+bestaudio/best[height<=720]"
            else:  # 480p
                fmt_selector = "bestvideo[height<=480]+bestaudio/best[height<=480]"

        result = {}

        def progress_hook(d):
            if self._cancel.is_set():
                raise Exception("Cancelled by user")
            if d["status"] == "downloading":
                raw = d.get("_percent_str", "0%").strip().replace("%", "")
                try:
                    pct = float(raw) * 0.8  # download = 0–80%
                except ValueError:
                    pct = 0
                speed = d.get("_speed_str", "")
                eta = d.get("_eta_str", "")
                self.after(0, lambda p=pct, s=speed, e=eta: self._set_status(
                    f"Downloading… {raw}%  {s}  ETA {e}", p))
            elif d["status"] == "finished":
                result["filename"] = d.get("filename") or d.get("info_dict", {}).get("_filename")
                self.after(0, lambda: self._set_status("Merging streams…", 82))
                self.after(0, lambda: self._log_write("  Streams merged."))

        def info_hook(info):
            title = info.get("title", "")
            ch = info.get("channel", "")
            self.after(0, lambda: self._log_write(f"  Title  : {title}"))
            self.after(0, lambda: self._log_write(f"  Channel: {ch}"))

        ydl_opts = {
            "format": fmt_selector,
            "outtmpl": os.path.join(self._output_dir, "%(title)s.%(ext)s"),
            "ffmpeg_location": os.path.dirname(ffmpeg_path()),
            "postprocessors": postprocessors,
            "progress_hooks": [progress_hook],
            "quiet": True,
            "no_warnings": True,
            "merge_output_format": ext if fmt not in ("MP3 (audio)", "M4A (audio)") else None,
        }

        self.after(0, lambda: self._log_write(f"▶  Fetching info for:\n   {url}\n"))

        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=False)
            info_hook(info)
            self.after(0, lambda: self._log_write(f"  Format : {quality}  →  {ext.upper()}\n"))
            ydl.download([url])

        # Resolve final filename
        filepath = result.get("filename")
        if not filepath:
            # fallback: find most-recently modified file in output dir
            files = [os.path.join(self._output_dir, f)
                     for f in os.listdir(self._output_dir)]
            files = [f for f in files if os.path.isfile(f)]
            if files:
                filepath = max(files, key=os.path.getmtime)

        self.after(0, lambda: self._set_status("Download complete", 85))
        return filepath

    # ── Compress ──────────────────────────────────────────────────────────────

    def _compress(self, input_path: str) -> str | None:
        preset_map = {
            "High (visually lossless)": 18,
            "Medium": 23,
            "Small": 28,
        }
        crf = preset_map.get(self._preset_var.get(), 18)

        base, _ = os.path.splitext(input_path)
        output_path = f"{base}_compressed.mkv"

        self.after(0, lambda: self._log_write(f"\n🔧  Compressing (CRF {crf}, H.265)…"))
        self.after(0, lambda: self._set_status("Compressing…", 87))

        # Get duration for progress
        try:
            probe = subprocess.run(
                [ffprobe_path(), "-v", "quiet", "-print_format", "json",
                 "-show_format", input_path],
                capture_output=True, text=True, timeout=30
            )
            duration = float(json.loads(probe.stdout)["format"]["duration"])
        except Exception:
            duration = None

        cmd = [
            ffmpeg_path(), "-y", "-i", input_path,
            "-c:v", "libx265",
            "-crf", str(crf),
            "-preset", "slow",
            "-tag:v", "hvc1",       # QuickTime / Apple compatibility
            "-c:a", "copy",         # audio untouched
            "-movflags", "+faststart",
            "-progress", "pipe:1",
            "-nostats",
            output_path,
        ]

        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True)

        while True:
            if self._cancel.is_set():
                proc.kill()
                self.after(0, lambda: self._log_write("  Compression cancelled."))
                return None

            line = proc.stdout.readline()
            if not line and proc.poll() is not None:
                break

            if line.startswith("out_time_ms=") and duration:
                try:
                    ms = int(line.split("=")[1].strip())
                    pct = 87 + (ms / 1_000_000 / duration) * 12
                    self.after(0, lambda p=pct: self._set_status("Compressing…", min(p, 99)))
                except ValueError:
                    pass

        if proc.returncode != 0:
            err = proc.stderr.read()
            raise RuntimeError(f"FFmpeg compression failed:\n{err[-800:]}")

        orig_mb = os.path.getsize(input_path) / 1_048_576
        comp_mb = os.path.getsize(output_path) / 1_048_576
        saved = (1 - comp_mb / orig_mb) * 100
        self.after(0, lambda: self._log_write(
            f"  Original : {orig_mb:.1f} MB\n"
            f"  Compressed: {comp_mb:.1f} MB\n"
            f"  Saved    : {saved:.1f}%"
        ))
        return output_path


# ── Entry point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    app = App()
    app.mainloop()
