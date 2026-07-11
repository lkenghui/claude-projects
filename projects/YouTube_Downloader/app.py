from __future__ import annotations

import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import threading
import subprocess
import sys
import os
import json
import shutil
import yt_dlp


# ── Helpers ──────────────────────────────────────────────────────────────────

# GUI apps launched via Finder/`open` get a minimal PATH that omits
# Homebrew's bin dirs, so shutil.which() alone misses a brew-installed ffmpeg.
_HOMEBREW_BIN_DIRS = ("/opt/homebrew/bin", "/usr/local/bin")

CONFIG_PATH = os.path.expanduser(
    "~/Library/Application Support/YouTube Downloader/settings.json"
)


class _Cancelled(Exception):
    """Raised internally to unwind a download/compress on user cancel."""


def _find_tool(name: str) -> str:
    bundled = os.path.join(getattr(sys, "_MEIPASS", os.path.dirname(__file__)), name)
    if os.path.isfile(bundled):
        return bundled
    found = shutil.which(name)
    if found:
        return found
    for bin_dir in _HOMEBREW_BIN_DIRS:
        candidate = os.path.join(bin_dir, name)
        if os.path.isfile(candidate):
            return candidate
    return name


def ffmpeg_path():
    return _find_tool("ffmpeg")


def ffprobe_path():
    return _find_tool("ffprobe")


def _collect_filepaths(info: dict) -> list[str]:
    """Recursively gather final output paths from a yt-dlp info dict.

    requested_downloads is populated in-place by yt-dlp after downloading
    and postprocessing (merge/convert/move) complete, so — unlike hook
    callbacks, which see a pre-postprocessing snapshot — it reflects the
    actual final file. Playlists nest per-video info under 'entries'.
    """
    if not info:
        return []
    paths = [
        rd["filepath"] for rd in (info.get("requested_downloads") or [])
        if rd.get("filepath")
    ]
    for entry in info.get("entries") or []:
        paths.extend(_collect_filepaths(entry))
    return paths


def _last_filepath(info: dict) -> str | None:
    paths = _collect_filepaths(info)
    return paths[-1] if paths else None


def _friendly_error(e: Exception) -> str:
    msg = str(e)
    low = msg.lower()
    if "private video" in low:
        return "This video is private."
    if "sign in to confirm your age" in low:
        return "This video is age-restricted and requires sign-in — not supported."
    if "video unavailable" in low or "this video is not available" in low:
        return "This video is unavailable (removed, region-blocked, or deleted)."
    if "403" in low:
        return ("YouTube refused the download (HTTP 403). This sometimes happens on "
                "shared/VPN networks — try again or switch networks.")
    if "urlopen error" in low or "timed out" in low or "connection" in low:
        return "Network error — check your internet connection and try again."
    if "ffmpeg" in low and "not found" in low:
        return "FFmpeg error — verify it's installed (brew install ffmpeg)."
    return msg


def _load_settings() -> dict:
    try:
        with open(CONFIG_PATH) as f:
            return json.load(f)
    except Exception:
        return {}


# ── Palette ──────────────────────────────────────────────────────────────────

BG = "#ffffff"
CARD = "#f8f7ff"
CARD_BORDER = "#e8e2fb"
ENTRY_BG = "#ffffff"
ENTRY_BORDER = "#d9d0f7"
FG = "#221f36"
MUTED = "#54506e"

ACCENT = "#7c3aed"              # primary purple — Download button, focus ring, progress bar
ACCENT_HOVER = "#6d28d9"
ACCENT_DISABLED = "#ddd0fb"
ACCENT_DISABLED_FG = "#7c6a9e"

BLUE = "#075985"                 # secondary — update link
BLUE_HOVER = "#0c4a6e"
BLUE_BG = "#e0f2fe"              # folder button
BLUE_BG_HOVER = "#bae6fd"
BLUE_TEXT = "#075985"

RED_BG = "#fef2f2"                # cancel button (while enabled)
RED_BG_HOVER = "#fee2e2"
RED_TEXT = "#b91c1c"

SUCCESS = "#16a34a"
ERROR = "#dc2626"

LOG_BG = "#211d36"
LOG_FG = "#d7d2ee"


# ── Main App ─────────────────────────────────────────────────────────────────

class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("YouTube Downloader")
        self.resizable(False, False)
        self.configure(bg=BG)

        self._settings = _load_settings()
        self._output_dir = self._settings.get("output_dir") or os.path.expanduser("~/Desktop")
        if not os.path.isdir(self._output_dir):
            self._output_dir = os.path.expanduser("~/Desktop")

        self._cancel = threading.Event()
        self._thread = None

        self._build_ui()
        self._update_compress_availability()
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    # ── UI ────────────────────────────────────────────────────────────────────

    def _build_ui(self):
        PAD = 16

        style = ttk.Style(self)
        style.theme_use("clam")
        style.configure("TProgressbar", troughcolor=ENTRY_BG, background=ACCENT,
                        thickness=8, borderwidth=0)
        style.configure("TCombobox", fieldbackground=ENTRY_BG, background=ENTRY_BG,
                        foreground=FG, arrowcolor=MUTED, selectbackground=ENTRY_BG,
                        selectforeground=FG, bordercolor=ENTRY_BORDER, borderwidth=1,
                        relief="flat")
        style.map("TCombobox",
                  fieldbackground=[("readonly", ENTRY_BG), ("disabled", CARD)],
                  foreground=[("disabled", MUTED)])

        # Plain tk.Button uses native Aqua chrome on macOS, which silently
        # ignores custom text colors — ttk buttons under "clam" fully
        # custom-draw instead, so the colors below actually render.
        style.configure("Accent.TButton", background=ACCENT, foreground="white",
                        font=("SF Pro Text", 12, "bold"), padding=(20, 8), borderwidth=0)
        style.map("Accent.TButton",
                  background=[("disabled", ACCENT_DISABLED), ("active", ACCENT_HOVER)],
                  foreground=[("disabled", ACCENT_DISABLED_FG)])

        style.configure("Cancel.TButton", background=RED_BG, foreground=RED_TEXT,
                        font=("SF Pro Text", 12, "bold"), padding=(20, 8), borderwidth=0)
        style.map("Cancel.TButton",
                  background=[("disabled", CARD), ("active", RED_BG_HOVER)],
                  foreground=[("disabled", MUTED)])

        style.configure("Folder.TButton", background=BLUE_BG, foreground=BLUE_TEXT,
                        font=("SF Pro Text", 10, "bold"), padding=(12, 8), borderwidth=0)
        style.map("Folder.TButton", background=[("active", BLUE_BG_HOVER)])

        outer = tk.Frame(self, bg=BG, padx=PAD, pady=PAD)
        outer.pack()

        # ── Header
        header = tk.Frame(outer, bg=BG)
        header.pack(fill="x", pady=(0, PAD))

        title_box = tk.Frame(header, bg=BG)
        title_box.pack(side="left")
        tk.Label(title_box, text="YouTube Downloader", font=("SF Pro Display", 20, "bold"),
                 bg=BG, fg=FG).pack(anchor="w")
        tk.Label(title_box, text="Download & compress YouTube videos at full fidelity",
                 font=("SF Pro Text", 11), bg=BG, fg=MUTED).pack(anchor="w", pady=(2, 0))

        self._update_btn = tk.Label(
            header, text="⟳  Update yt-dlp", font=("SF Pro Text", 10, "bold"),
            bg=BG, fg=BLUE, cursor="pointinghand",
        )
        self._update_btn.pack(side="right", anchor="ne", pady=(2, 0))
        self._update_btn.bind("<Button-1>", lambda e: self._check_update())
        self._add_label_hover(self._update_btn, BLUE, BLUE_HOVER)

        # ── URL card
        self._card(outer, PAD)

        # ── Log
        log_frame = tk.Frame(outer, bg=CARD, bd=0, highlightthickness=1,
                              highlightbackground=CARD_BORDER)
        log_frame.pack(fill="x", pady=(PAD, 0))

        tk.Label(log_frame, text="Log", font=("SF Pro Text", 11, "bold"),
                 bg=CARD, fg=FG, padx=12, pady=8).pack(anchor="w")

        self._log = tk.Text(log_frame, height=10, width=68, bg=LOG_BG, fg=LOG_FG,
                            font=("SF Mono", 10), relief="flat", state="disabled",
                            padx=10, pady=8, wrap="word")
        self._log.pack(padx=12, pady=(0, 12))

        # ── Progress
        prog_frame = tk.Frame(outer, bg=BG)
        prog_frame.pack(fill="x", pady=(PAD, 0))

        self._status_var = tk.StringVar(value="Ready")
        self._status_label = tk.Label(prog_frame, textvariable=self._status_var,
                                       font=("SF Pro Text", 10), bg=BG, fg=MUTED)
        self._status_label.pack(anchor="w")
        self._progress = ttk.Progressbar(prog_frame, mode="determinate", length=600)
        self._progress.pack(fill="x", pady=(4, 0))

        # ── Buttons
        btn_frame = tk.Frame(outer, bg=BG)
        btn_frame.pack(fill="x", pady=(PAD, 0))

        self._dl_btn = ttk.Button(
            btn_frame, text="Download", style="Accent.TButton",
            cursor="pointinghand", command=self._start,
        )
        self._dl_btn.pack(side="left")

        self._cancel_btn = ttk.Button(
            btn_frame, text="Cancel", style="Cancel.TButton",
            cursor="pointinghand", state="disabled", command=self._do_cancel,
        )
        self._cancel_btn.pack(side="left", padx=(8, 0))

        self._folder_btn = ttk.Button(
            btn_frame, text=self._folder_label(), style="Folder.TButton",
            cursor="pointinghand", command=self._choose_folder,
        )
        self._folder_btn.pack(side="right")

    def _folder_label(self) -> str:
        folder = self._output_dir
        label = folder if len(folder) < 40 else "…" + folder[-37:]
        return f"📁  {label}"

    def _add_label_hover(self, widget, normal_fg, hover_fg):
        widget.bind("<Enter>", lambda e: widget.configure(fg=hover_fg))
        widget.bind("<Leave>", lambda e: widget.configure(fg=normal_fg))

    def _card(self, parent, PAD):
        card = tk.Frame(parent, bg=CARD, pady=12, padx=12, highlightthickness=1,
                         highlightbackground=CARD_BORDER)
        card.pack(fill="x", pady=(0, PAD))

        # URL
        tk.Label(card, text="YouTube URL", font=("SF Pro Text", 11, "bold"),
                 bg=CARD, fg=FG).grid(row=0, column=0, sticky="w", pady=(0, 4))
        self._url_var = tk.StringVar()
        url_entry = tk.Entry(card, textvariable=self._url_var, font=("SF Pro Text", 12),
                             bg=ENTRY_BG, fg=FG, relief="flat", insertbackground=FG,
                             highlightthickness=1, highlightbackground=ENTRY_BORDER,
                             highlightcolor=ACCENT, width=55)
        url_entry.grid(row=1, column=0, columnspan=3, sticky="ew", ipady=6, pady=(0, 12))

        # Quality
        tk.Label(card, text="Video Quality", font=("SF Pro Text", 11, "bold"),
                 bg=CARD, fg=FG).grid(row=2, column=0, sticky="w", pady=(0, 4))
        self._quality_var = tk.StringVar(value=self._settings.get("quality", "Best (4K/HDR)"))
        quality_cb = ttk.Combobox(card, textvariable=self._quality_var, state="readonly",
                                  values=["Best (4K/HDR)", "1080p", "720p", "480p", "Audio Only"],
                                  width=22, font=("SF Pro Text", 11))
        quality_cb.grid(row=3, column=0, sticky="w", pady=(0, 12))
        quality_cb.bind("<<ComboboxSelected>>", lambda e: self._update_compress_availability())

        # Compress toggle
        tk.Label(card, text="Compress After Download", font=("SF Pro Text", 11, "bold"),
                 bg=CARD, fg=FG).grid(row=2, column=1, sticky="w", padx=(24, 0), pady=(0, 4))
        self._compress_var = tk.BooleanVar(value=self._settings.get("compress", False))
        self._compress_chk = tk.Checkbutton(
            card, variable=self._compress_var, bg=CARD, activebackground=CARD,
            selectcolor="#ede9fe", command=self._toggle_compress, cursor="pointinghand",
        )
        self._compress_chk.grid(row=3, column=1, sticky="w", padx=(24, 0), pady=(0, 12))

        # Compression preset
        tk.Label(card, text="Compression Preset", font=("SF Pro Text", 11, "bold"),
                 bg=CARD, fg=FG).grid(row=2, column=2, sticky="w", padx=(24, 0), pady=(0, 4))
        self._preset_var = tk.StringVar(value=self._settings.get("preset", "High (visually lossless)"))
        self._preset_cb = ttk.Combobox(card, textvariable=self._preset_var, state="disabled",
                                       values=["High (visually lossless)", "Medium", "Small"],
                                       width=22, font=("SF Pro Text", 11))
        self._preset_cb.grid(row=3, column=2, sticky="w", padx=(24, 0), pady=(0, 12))

        # Format
        tk.Label(card, text="Output Format", font=("SF Pro Text", 11, "bold"),
                 bg=CARD, fg=FG).grid(row=4, column=0, sticky="w", pady=(0, 4))
        self._fmt_var = tk.StringVar(value=self._settings.get("format", "MKV"))
        fmt_cb = ttk.Combobox(card, textvariable=self._fmt_var, state="readonly",
                               values=["MKV", "MP4", "MP3 (audio)", "M4A (audio)"],
                               width=22, font=("SF Pro Text", 11))
        fmt_cb.grid(row=5, column=0, sticky="w")
        fmt_cb.bind("<<ComboboxSelected>>", lambda e: self._update_compress_availability())

        # Playlist toggle
        tk.Label(card, text="Include Playlist", font=("SF Pro Text", 11, "bold"),
                 bg=CARD, fg=FG).grid(row=4, column=1, sticky="w", padx=(24, 0), pady=(0, 4))
        self._playlist_var = tk.BooleanVar(value=self._settings.get("playlist", False))
        playlist_chk = tk.Checkbutton(
            card, variable=self._playlist_var, bg=CARD, activebackground=CARD,
            selectcolor="#ede9fe", command=self._update_compress_availability, cursor="pointinghand",
        )
        playlist_chk.grid(row=5, column=1, sticky="w", padx=(24, 0))

    # ── Logic ─────────────────────────────────────────────────────────────────

    def _toggle_compress(self):
        state = "readonly" if self._compress_var.get() else "disabled"
        self._preset_cb.configure(state=state)

    def _update_compress_availability(self):
        audio_only = (self._quality_var.get() == "Audio Only"
                      or self._fmt_var.get() in ("MP3 (audio)", "M4A (audio)"))
        playlist = self._playlist_var.get()
        allowed = not audio_only and not playlist

        self._compress_chk.configure(state="normal" if allowed else "disabled")
        if not allowed:
            self._compress_var.set(False)
        self._toggle_compress()

    def _choose_folder(self):
        folder = filedialog.askdirectory(initialdir=self._output_dir)
        if folder:
            self._output_dir = folder
            self._folder_btn.configure(text=self._folder_label())

    def _log_write(self, msg: str):
        self._log.configure(state="normal")
        self._log.insert("end", msg + "\n")
        self._log.see("end")
        self._log.configure(state="disabled")

    def _set_status(self, text: str, pct: float = None, kind: str = "running"):
        self._status_var.set(text)
        color = {"running": MUTED, "success": SUCCESS, "error": ERROR, "idle": MUTED}.get(kind, MUTED)
        self._status_label.configure(fg=color)
        if pct is not None:
            self._progress["value"] = pct

    def _set_buttons(self, running: bool):
        self._dl_btn.configure(state="disabled" if running else "normal")
        self._cancel_btn.configure(state="normal" if running else "disabled")

    def _do_cancel(self):
        self._cancel.set()
        self._log_write("⚠  Cancel requested…")

    def _on_close(self):
        self._save_settings()
        self.destroy()

    def _save_settings(self):
        data = {
            "quality": self._quality_var.get(),
            "format": self._fmt_var.get(),
            "compress": self._compress_var.get(),
            "preset": self._preset_var.get(),
            "playlist": self._playlist_var.get(),
            "output_dir": self._output_dir,
        }
        try:
            os.makedirs(os.path.dirname(CONFIG_PATH), exist_ok=True)
            with open(CONFIG_PATH, "w") as f:
                json.dump(data, f, indent=2)
        except OSError:
            pass

    def _check_update(self):
        if getattr(sys, "frozen", False):
            messagebox.showinfo(
                "Update yt-dlp",
                "This is a packaged app, so it can't update itself.\n\n"
                "To get the latest yt-dlp:\n"
                "  1. cd YouTube_Downloader\n"
                "  2. venv/bin/pip install -U yt-dlp\n"
                "  3. ./build.sh\n\n"
                "YouTube changes frequently, so keeping yt-dlp current avoids "
                "download failures.",
            )
            return

        self._update_btn.configure(text="⟳  Checking…", fg=MUTED)

        def worker():
            try:
                proc = subprocess.run(
                    [sys.executable, "-m", "pip", "install", "-U", "yt-dlp"],
                    capture_output=True, text=True, timeout=90,
                )
                out = proc.stdout + proc.stderr
                if "Successfully installed" in out:
                    msg = "yt-dlp updated. Restart the app to use the new version."
                elif proc.returncode == 0:
                    msg = f"Already up to date (yt-dlp {yt_dlp.version.__version__})."
                else:
                    msg = f"Update failed:\n{out[-500:]}"
            except Exception as e:
                msg = f"Update failed: {e}"
            self.after(0, lambda: messagebox.showinfo("Update yt-dlp", msg))
            self.after(0, lambda: self._update_btn.configure(text="⟳  Update yt-dlp", fg=BLUE))

        threading.Thread(target=worker, daemon=True).start()

    def _start(self):
        url = self._url_var.get().strip()
        if not url:
            messagebox.showwarning("No URL", "Please enter a YouTube URL.")
            return
        if not os.path.isfile(ffmpeg_path()):
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
        self._set_status("Starting…", 0, kind="running")

        self._thread = threading.Thread(target=self._run, args=(url,), daemon=True)
        self._thread.start()

    def _run(self, url: str):
        try:
            filepath, count = self._download(url)
            if filepath and self._compress_var.get():
                filepath = self._compress(filepath)
            if filepath:
                self.after(0, lambda: self._set_status("Done!", 100, kind="success"))
                if count > 1:
                    msg = f"\n✅  Downloaded {count} files to: {self._output_dir}"
                else:
                    msg = f"\n✅  Saved to: {filepath}"
                self.after(0, lambda m=msg: self._log_write(m))
        except _Cancelled:
            self.after(0, lambda: self._set_status("Cancelled", 0, kind="idle"))
            self.after(0, lambda: self._log_write("\n⚠  Cancelled."))
        except Exception as e:
            friendly = _friendly_error(e)
            self.after(0, lambda: self._log_write(f"\n❌  Error: {friendly}"))
            self.after(0, lambda: self._set_status("Error", 0, kind="error"))
        finally:
            self.after(0, lambda: self._set_buttons(False))
            self._save_settings()

    # ── Download ──────────────────────────────────────────────────────────────

    def _download(self, url: str):
        quality = self._quality_var.get()
        fmt = self._fmt_var.get()
        include_playlist = self._playlist_var.get()

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

        logged_info = {"done": False}

        def progress_hook(d):
            if self._cancel.is_set():
                raise _Cancelled()

            info = d.get("info_dict") or {}
            if not logged_info["done"] and info:
                logged_info["done"] = True
                if info.get("playlist"):
                    name = info.get("playlist_title") or info.get("playlist") or ""
                    total = info.get("playlist_count", "?")
                    self.after(0, lambda: self._log_write(f"  Playlist: {name}"))
                    self.after(0, lambda: self._log_write(f"  Videos  : {total}\n"))
                else:
                    title = info.get("title", "")
                    ch = info.get("channel", "")
                    self.after(0, lambda: self._log_write(f"  Title  : {title}"))
                    self.after(0, lambda: self._log_write(f"  Channel: {ch}\n"))

            if d["status"] == "downloading":
                downloaded = d.get("downloaded_bytes") or 0
                total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                frac = (downloaded / total) if total else 0
                pct = frac * 80
                speed = d.get("speed")
                speed_str = f"{speed / 1_048_576:.1f} MB/s" if speed else "…"
                eta = d.get("eta")
                eta_str = f"{eta}s" if eta is not None else "…"
                self.after(0, lambda p=pct, f=frac, s=speed_str, e=eta_str: self._set_status(
                    f"Downloading… {f * 100:.0f}%  {s}  ETA {e}", p))
            elif d["status"] == "finished":
                self.after(0, lambda: self._set_status("Processing…", 82))
                self.after(0, lambda: self._log_write("  ✓ Stream ready, post-processing…"))

        ydl_opts = {
            "format": fmt_selector,
            "outtmpl": os.path.join(self._output_dir, "%(title)s.%(ext)s"),
            "ffmpeg_location": os.path.dirname(ffmpeg_path()),
            "postprocessors": postprocessors,
            "progress_hooks": [progress_hook],
            "quiet": True,
            "no_warnings": True,
            "noplaylist": not include_playlist,
            "merge_output_format": ext if fmt not in ("MP3 (audio)", "M4A (audio)") else None,
        }

        self.after(0, lambda: self._log_write(f"▶  Fetching info for:\n   {url}\n"))
        self.after(0, lambda: self._log_write(f"  Format : {quality}  →  {ext.upper()}\n"))

        # A single extract_info(download=True) call — rather than a probing
        # extract_info(download=False) followed by download() — is what lets
        # us read back requested_downloads below: it's populated in-place on
        # the same info dict that downloads/postprocessing mutate, so it
        # reflects the true final path (unlike the hooks above, whose
        # info_dict is a pre-postprocessing snapshot).
        with yt_dlp.YoutubeDL(ydl_opts) as ydl:
            info = ydl.extract_info(url, download=True)

        filepath = _last_filepath(info)
        count = len(_collect_filepaths(info))
        if not filepath:
            # fallback: find most-recently modified file in output dir
            files = [os.path.join(self._output_dir, f)
                     for f in os.listdir(self._output_dir)]
            files = [f for f in files if os.path.isfile(f)]
            if files:
                filepath = max(files, key=os.path.getmtime)
                count = 1

        self.after(0, lambda: self._set_status("Download complete", 85))
        return filepath, count

    # ── Compress ──────────────────────────────────────────────────────────────

    def _compress(self, input_path: str) -> str | None:
        preset_map = {
            "High (visually lossless)": 18,
            "Medium": 23,
            "Small": 28,
        }
        crf = preset_map.get(self._preset_var.get(), 18)

        container = self._fmt_var.get().lower()
        if container not in ("mkv", "mp4"):
            container = "mkv"
        base, _ = os.path.splitext(input_path)
        output_path = f"{base}_compressed.{container}"

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
                raise _Cancelled()

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
