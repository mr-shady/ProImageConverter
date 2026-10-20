"""Pro Image Converter — desktop UI.

The interface is intentionally built around three ideas:

* a calm, clearly grouped layout (settings on the right, workspace on the left),
* no blocking dialogs for routine feedback (a toast replaces end-of-run alerts),
* folder scanning and conversion always run off the UI thread, so the window
  never freezes and the interface feels instant.
"""

from __future__ import annotations

import os
import queue
import re
import subprocess
import sys
import threading
import time
import tkinter as tk
from pathlib import Path
from typing import Callable

import customtkinter as ctk
from PIL import Image, ImageColor
from tkinter import colorchooser, filedialog, messagebox
from tkinter import font as tkfont

from converter_core import (
    ALPHA_UNSUPPORTED,
    APP_NAME,
    APP_VERSION,
    BatchImageConverter,
    ConversionOptions,
    ConversionResult,
    HEIF_ENABLED,
    LOSSY_FORMATS,
    RAW_ENABLED,
    SVG_ENABLED,
    available_output_formats,
)
from settings_store import load_settings, save_settings


# ---------------------------------------------------------------------------
# Theme
# ---------------------------------------------------------------------------

# CustomTkinter accepts (light, dark) tuples everywhere, so a single palette
# keeps both appearance modes consistent.
PALETTE: dict[str, str | tuple[str, str]] = {
    "bg": ("#F2F5FA", "#090C12"),
    "header": ("#FFFFFF", "#0D1119"),
    "sidebar": ("#FFFFFF", "#0D1119"),
    "surface": ("#FFFFFF", "#151C28"),
    "surface_muted": ("#F5F8FD", "#101622"),
    "surface_hover": ("#EDF2FA", "#1C2432"),
    "border": ("#DCE3EF", "#2A3547"),
    "text": ("#101828", "#E7ECF5"),
    "muted": ("#667085", "#93A0B6"),
    "faint": ("#98A2B3", "#64718A"),
    "accent": ("#2563EB", "#3B82F6"),
    "accent_hover": ("#1D4ED8", "#2F6FE0"),
    "accent_soft": ("#E8F0FE", "#172646"),
    "success": ("#059669", "#34D399"),
    "success_soft": ("#E6F7F0", "#10281F"),
    "warning": ("#B45309", "#FBBF24"),
    "warning_soft": ("#FEF3E2", "#2A2113"),
    "danger": ("#DC2626", "#F87171"),
    "danger_soft": ("#FDECEC", "#2A1517"),
    "skip": ("#64748B", "#94A3B8"),
    "white": "#FFFFFF",
}

FORMAT_NOTES = {
    "JPG": "سازگار با همه دستگاه‌ها • شفافیت ندارد",
    "PNG": "بدون افت کیفیت • پشتیبانی کامل از شفافیت",
    "WEBP": "تعادل بهترین حجم و کیفیت برای وب",
    "AVIF": "فشرده‌سازی نسل جدید • حجم بسیار کم",
    "HEIC": "مناسب آیفون و مک • حجم کم",
    "TIFF": "کیفیت آرشیوی • حجم زیاد",
    "BMP": "بدون فشرده‌سازی • سازگاری قدیمی",
    "GIF": "انیمیشن ساده با ۲۵۶ رنگ",
    "ICO": "آیکن ویندوز در چند اندازه",
    "PDF": "سند تصویری قابل چاپ",
}

TAB_NAMES = ("خروجی", "پردازش", "اندازه")
THEME_NAMES = ("تیره", "روشن", "سیستم")
THEME_MODES = {"تیره": "dark", "روشن": "light", "سیستم": "system"}

_FA_DIGITS = str.maketrans("0123456789", "۰۱۲۳۴۵۶۷۸۹")


def to_fa(value: object) -> str:
    """Format a number for display with Persian digits."""
    return str(value).translate(_FA_DIGITS).replace(".", "٫")


def shorten_text(value: str, limit: int = 48) -> str:
    text = str(value)
    if len(text) <= limit:
        return text
    return text[: max(4, limit - 1)] + "…"


def short_path(value: str, limit: int = 42) -> str:
    """Middle-ellipsis for paths: keeps the drive and the deepest folders."""
    text = str(value).strip().rstrip("\\/")
    if len(text) <= limit:
        return text

    drive = ""
    rest = text
    if len(text) >= 3 and text[1] == ":":
        drive = text[:2]
        rest = text[3:] if text[2] in "\\/" else text[2:]
    elif text.startswith("\\\\"):
        drive = "\\\\"
        rest = text[2:]

    parts = [part for part in rest.replace("/", "\\").split("\\") if part]
    prefix = f"{drive}\\…\\" if drive and drive != "\\\\" else (f"{drive}…\\" if drive else "…\\")

    tail: list[str] = []
    for part in reversed(parts):
        candidate = [part, *tail]
        if len(prefix) + len("\\".join(candidate)) > limit:
            break
        tail = candidate

    if not tail:
        return "…" + text[-(limit - 1):]
    return prefix + "\\".join(tail)


def format_duration(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    if seconds < 10:
        text = f"{seconds:.1f} ثانیه"
    elif seconds < 60:
        text = f"{round(seconds)} ثانیه"
    elif seconds < 3600:
        minutes = int(seconds // 60)
        remainder = int(round(seconds % 60))
        text = f"{minutes} دقیقه" + (f" و {remainder} ثانیه" if remainder else "")
    else:
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        text = f"{hours} ساعت و {minutes} دقیقه"
    return to_fa(text)


def resource_path(relative_path: str) -> Path:
    """Resolve bundled assets in source mode and in a PyInstaller EXE."""
    base = Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent))
    return base / relative_path


# ---------------------------------------------------------------------------
# Toast notification
# ---------------------------------------------------------------------------


class Toast:
    """Small in-app notification that slides in and hides itself.

    It never grabs focus and never waits for a click; long jobs finish and the
    user keeps working. An optional action button opens e.g. the output folder.
    """

    BASE_Y = -84
    WIDTH = 442

    def __init__(self, app) -> None:
        self.app = app
        master = app.workspace
        self._visible = False
        self._duration = 0
        self._remaining = 0
        self._deadline = 0.0
        self._action_cb: Callable[[], None] | None = None
        self._tick_job: str | None = None
        self._anim_job: str | None = None
        self._hide_job: str | None = None

        self.frame = ctk.CTkFrame(
            master,
            width=self.WIDTH,
            fg_color=PALETTE["surface"],
            corner_radius=14,
            border_width=1,
            border_color=PALETTE["border"],
        )
        self.frame.grid_columnconfigure(1, weight=1)

        self._close = ctk.CTkButton(
            self.frame,
            text="✕",
            width=26,
            height=26,
            corner_radius=8,
            fg_color="transparent",
            hover_color=PALETTE["surface_hover"],
            text_color=PALETTE["muted"],
            font=ctk.CTkFont(size=11),
            command=self.hide,
        )
        self._close.grid(row=0, column=0, sticky="nw", padx=(8, 0), pady=(8, 0))

        self._badge = ctk.CTkFrame(self.frame, width=32, height=32, corner_radius=16,
                                   fg_color=PALETTE["success_soft"])
        self._badge.grid_propagate(False)
        self._badge.grid(row=0, column=2, rowspan=2, sticky="n", padx=(10, 14), pady=14)

        self._glyph = ctk.CTkLabel(
            self._badge,
            text="✓",
            font=ctk.CTkFont(size=16, weight="bold"),
            text_color=PALETTE["success"],
        )
        self._glyph.place(relx=0.5, rely=0.5, anchor="center")

        self._title = ctk.CTkLabel(
            self.frame,
            text="",
            anchor="e",
            justify="right",
            wraplength=340,
        )
        self._title.grid(row=0, column=1, sticky="ew", padx=(4, 0), pady=(10, 0))

        self._body = ctk.CTkLabel(
            self.frame,
            text="",
            anchor="e",
            justify="right",
            wraplength=340,
            text_color=PALETTE["muted"],
        )
        self._body.grid(row=1, column=1, sticky="ew", padx=(4, 0), pady=(2, 6))

        self._action = ctk.CTkButton(
            self.frame,
            text="",
            width=132,
            height=30,
            corner_radius=8,
            fg_color="transparent",
            border_width=1,
            border_color=PALETTE["border"],
            hover_color=PALETTE["surface_hover"],
            text_color=PALETTE["text"],
            command=self._run_action,
        )
        self._action.grid(row=2, column=1, sticky="e", padx=(4, 0), pady=(0, 8))
        self._action.grid_remove()

        self._bar = ctk.CTkProgressBar(
            self.frame,
            height=3,
            corner_radius=0,
            progress_color=PALETTE["accent"],
            fg_color=PALETTE["surface"],
        )
        self._bar.grid(row=3, column=0, columnspan=3, sticky="ew", padx=2, pady=(0, 2))
        self._bar.set(0)

        # Forces a stable toast width even though its content is narrower.
        ctk.CTkFrame(
            self.frame,
            width=self.WIDTH - 4,
            height=1,
            fg_color="transparent",
            corner_radius=0,
        ).grid(row=4, column=0, columnspan=3)

        self.frame.place_forget()

    # -- public API ---------------------------------------------------------

    def show(
        self,
        title: str,
        message: str = "",
        kind: str = "success",
        action: tuple[str, Callable[[], None]] | None = None,
        duration: int = 6000,
    ) -> None:
        glyphs = {
            "success": ("✓", PALETTE["success"], PALETTE["success_soft"]),
            "warning": ("!", PALETTE["warning"], PALETTE["warning_soft"]),
            "error": ("✕", PALETTE["danger"], PALETTE["danger_soft"]),
            "info": ("i", PALETTE["accent"], PALETTE["accent_soft"]),
        }
        glyph, color, soft = glyphs.get(kind, glyphs["info"])

        self._cancel_jobs()
        self._visible = True
        self._duration = max(1200, int(duration))
        self._remaining = self._duration
        self._deadline = time.monotonic() + self._duration / 1000

        self._glyph.configure(text=glyph, text_color=color)
        self._badge.configure(fg_color=soft)
        self._title.configure(
            text=title,
            font=self.app.app_font(13, "bold"),
            text_color=PALETTE["text"],
        )
        self._body.configure(
            text=message,
            font=self.app.app_font(11),
            text_color=PALETTE["muted"],
        )

        if action is not None:
            label, callback = action
            self._action_cb = callback
            self._action.configure(text=label)
            self._action.grid()
        else:
            self._action_cb = None
            self._action.grid_remove()

        self._bar.set(1)
        self.frame.place(relx=0.5, rely=1.0, anchor="s", y=self.BASE_Y + 26)
        self.frame.lift()
        self._animate(0)
        self._tick()

    def hide(self) -> None:
        if not self._visible:
            return
        self._visible = False
        self._cancel_jobs()
        self.frame.place(relx=0.5, rely=1.0, anchor="s", y=self.BASE_Y + 18)
        self._hide_job = self.frame.after(130, self.frame.place_forget)

    def destroy(self) -> None:
        self._cancel_jobs()
        try:
            self.frame.destroy()
        except Exception:
            pass

    # -- internals ----------------------------------------------------------

    def _run_action(self) -> None:
        callback = self._action_cb
        self.hide()
        if callback is not None:
            callback()

    def _animate(self, step: int) -> None:
        if not self._visible:
            return
        steps = 7
        progress = min(1.0, step / steps)
        eased = 1 - (1 - progress) ** 3
        y = int(self.BASE_Y + 26 * (1 - eased))
        self.frame.place(relx=0.5, rely=1.0, anchor="s", y=y)
        if step < steps:
            self._anim_job = self.frame.after(16, lambda: self._animate(step + 1))

    def _tick(self) -> None:
        if not self._visible or self._duration <= 0:
            return
        # Wall-clock based so Windows timer throttling in the background can
        # delay the toast but never stretch its lifetime.
        remaining = self._deadline - time.monotonic()
        self._remaining = max(0, int(remaining * 1000))
        self._bar.set(max(0.0, min(1.0, remaining / (self._duration / 1000))))
        if remaining <= 0:
            self.hide()
            return
        delay = max(30, min(100, int(remaining * 1000)))
        self._tick_job = self.frame.after(delay, self._tick)

    def _cancel_jobs(self) -> None:
        for job in (self._tick_job, self._anim_job, self._hide_job):
            if job is not None:
                try:
                    self.frame.after_cancel(job)
                except Exception:
                    pass
        self._tick_job = None
        self._anim_job = None
        self._hide_job = None


class SlimScrollbar(tk.Canvas):
    """Thin rounded scrollbar drawn on a canvas.

    Tk's native scrollbar paints a bright system-colored thumb that clashes
    with both themes, and CustomTkinter's version redraws far too eagerly.
    This one is a couple of canvas items and follows the app palette.
    """

    MIN_THUMB = 30

    def __init__(
        self,
        master,
        command: Callable | None = None,
        *,
        width: int = 8,
        background: str = "#101622",
        thumb: str = "#2A3547",
        hover: str = "#93A0B6",
        **kwargs,
    ) -> None:
        super().__init__(
            master,
            width=width,
            highlightthickness=0,
            borderwidth=0,
            background=background,
            takefocus=0,
            cursor="arrow",
            **kwargs,
        )
        self._command = command
        self._first = 0.0
        self._last = 1.0
        self._background = background
        self._thumb = thumb
        self._hover = hover
        self._hovering = False
        self._drag_offset = 0.0
        self._thumb_box: tuple[float, float, float, float] | None = None

        self.bind("<Configure>", lambda _event: self._draw())
        self.bind("<Button-1>", self._on_press)
        self.bind("<B1-Motion>", self._on_drag)
        self.bind("<Enter>", self._on_enter)
        self.bind("<Leave>", self._on_leave)

    # -- scrollbar protocol -------------------------------------------------

    def set(self, first, last) -> None:
        try:
            self._first = float(first)
            self._last = float(last)
        except (TypeError, ValueError):
            return
        self._draw()

    def get(self) -> tuple[float, float]:
        return self._first, self._last

    def restyle(self, background: str, thumb: str, hover: str) -> None:
        self._background = background
        self._thumb = thumb
        self._hover = hover
        self.configure(background=background)
        self._draw()

    # -- drawing ------------------------------------------------------------

    def _draw(self) -> None:
        if not self.winfo_exists():
            return
        self.delete("all")
        geometry = self._thumb_geometry()
        self._thumb_box = geometry
        if geometry is None:
            return

        x0, y0, x1, y1 = geometry
        color = self._hover if self._hovering else self._thumb
        radius = max(1, int((x1 - x0) / 2))
        self.create_rectangle(x0, y0 + radius, x1, y1 - radius, fill=color, outline=color)
        self.create_oval(x0, y0, x1, y0 + 2 * radius, fill=color, outline=color)
        self.create_oval(x0, y1 - 2 * radius, x1, y1, fill=color, outline=color)

    def _thumb_geometry(self) -> tuple[float, float, float, float] | None:
        height = self.winfo_height()
        width = self.winfo_width()
        span = self._last - self._first
        if height <= 4 or span >= 0.998 or span <= 0.0:
            return None

        thumb_height = max(self.MIN_THUMB, int(height * span))
        thumb_height = min(height, thumb_height)
        travel = height - thumb_height
        fraction = self._first / max(1e-9, 1.0 - span)
        top = max(0.0, min(travel, travel * fraction))
        inset = 1
        return inset, top, max(inset + 1, width - inset), top + thumb_height

    # -- dragging -----------------------------------------------------------

    def _on_press(self, event) -> None:
        geometry = self._thumb_box
        if geometry is None:
            return
        x0, y0, x1, y1 = geometry
        if y0 <= event.y <= y1:
            self._drag_offset = event.y - y0
            return
        self._drag_offset = (y1 - y0) / 2
        self._scroll_to(event.y)

    def _on_drag(self, event) -> None:
        self._scroll_to(event.y)

    def _scroll_to(self, pointer_y: float) -> None:
        if self._command is None:
            return
        geometry = self._thumb_box
        if geometry is None:
            return
        thumb_height = geometry[3] - geometry[1]
        travel = max(1.0, self.winfo_height() - thumb_height)
        fraction = (pointer_y - self._drag_offset) / travel
        self._command("moveto", max(0.0, min(1.0, fraction)))

    def _on_enter(self, _event) -> None:
        self._hovering = True
        self._draw()

    def _on_leave(self, _event) -> None:
        self._hovering = False
        self._draw()


class SettingsPane:
    """Scrollable container for one settings tab.

    A plain Tk canvas is used instead of ``CTkScrollableFrame`` because it
    keeps the widget tree small and lets the scrollbar disappear when the
    content already fits.
    """

    CONTENT_WIDTH = 350
    GUTTER = 12

    def __init__(self, app: "ProImageConverterApp", master) -> None:
        self.app = app

        self.outer = ctk.CTkFrame(master, fg_color="transparent")
        self.outer.grid(row=0, column=0, sticky="nsew")
        self.outer.grid_columnconfigure(0, weight=1)
        self.outer.grid_rowconfigure(0, weight=1)

        self.canvas = tk.Canvas(
            self.outer,
            highlightthickness=0,
            borderwidth=0,
            bg=app._theme_color("sidebar"),
        )
        self.canvas.grid(row=0, column=0, sticky="nsew")

        self.scrollbar = SlimScrollbar(
            self.outer,
            command=self.canvas.yview,
            width=9,
            background=app._theme_color("sidebar"),
            thumb=app._theme_color("faint"),
            hover=app._theme_color("muted"),
        )
        self.scrollbar.place(x=0, y=0, relheight=1.0)
        self.canvas.configure(yscrollcommand=self.scrollbar.set)

        self.inner = ctk.CTkFrame(
            self.canvas,
            width=self.CONTENT_WIDTH,
            fg_color=PALETTE["sidebar"],
            corner_radius=0,
        )
        self.window = self.canvas.create_window(
            (self.GUTTER, 0),
            window=self.inner,
            anchor="nw",
            width=self.CONTENT_WIDTH,
        )

        self._scrollbar_visible = True
        self._refresh_job: str | None = None
        self.inner.bind("<Configure>", self._on_inner_configure)
        self.canvas.bind("<Configure>", self._on_canvas_configure)
        self.outer.after(60, self._refresh_scrollbar)

    def _on_inner_configure(self, _event=None) -> None:
        self.canvas.configure(scrollregion=self.canvas.bbox("all"))
        self._schedule_refresh()

    def _on_canvas_configure(self, _event) -> None:
        self._schedule_refresh()

    def _schedule_refresh(self) -> None:
        if self._refresh_job is not None:
            return
        self._refresh_job = self.outer.after_idle(self._refresh_scrollbar)

    def _refresh_scrollbar(self) -> None:
        self._refresh_job = None
        try:
            needed = self.inner.winfo_reqheight() > self.canvas.winfo_height() + 2
        except Exception:
            return
        if needed == self._scrollbar_visible:
            return
        self._scrollbar_visible = needed
        if needed:
            self.scrollbar.place(x=0, y=0, relheight=1.0)
        else:
            self.scrollbar.place_forget()

    def bind_wheel(self) -> None:
        self._bind_wheel_recursive(self.inner)
        self.canvas.bind("<MouseWheel>", self._on_wheel, add="+")

    def _bind_wheel_recursive(self, widget) -> None:
        try:
            widget.bind("<MouseWheel>", self._on_wheel, add="+")
        except Exception:
            pass
        for child in widget.winfo_children():
            self._bind_wheel_recursive(child)

    def _on_wheel(self, event) -> None:
        if not self.canvas.winfo_exists():
            return
        steps = -1 if event.delta > 0 else 1
        self.canvas.yview_scroll(steps, "units")

    def restyle(self) -> None:
        background = self.app._theme_color("sidebar")
        self.canvas.configure(bg=background)
        self.scrollbar.restyle(
            background,
            self.app._theme_color("faint"),
            self.app._theme_color("muted"),
        )


# ---------------------------------------------------------------------------
# Application
# ---------------------------------------------------------------------------


class ProImageConverterApp(ctk.CTk):
    SIDEBAR_WIDTH = 402

    def __init__(self) -> None:
        super().__init__()

        self.title(f"{APP_NAME} {APP_VERSION}")
        self.configure(fg_color=PALETTE["bg"])
        self.minsize(1060, 660)
        self.protocol("WM_DELETE_WINDOW", self.on_close)

        self.saved_settings = load_settings()
        self._settings_save_job: str | None = None
        self._scan_after_job: str | None = None
        self._poll_job: str | None = None
        self._scan_generation = 0
        self._run_active = False
        self._closing = False
        self._pending_log: list[str] = []
        self._last_progress: tuple[int, int, ConversionResult] | None = None
        self._pending_done: tuple[list[ConversionResult], bool, float] | None = None
        self._pending_fatal: Exception | None = None
        self._run_started_at = 0.0
        self._icon_photo: tk.PhotoImage | None = None
        self._logo_source: Image.Image | None = None
        self._logo_ctk: ctk.CTkImage | None = None
        self._font_cache: dict[tuple[int, str], ctk.CTkFont] = {}
        self._built_tabs: set[str] = set()
        self.tab_frames: dict[str, ctk.CTkFrame] = {}
        self.tab_builders: dict[str, Callable[[ctk.CTkFrame], None]] = {}
        self.format_buttons: dict[str, ctk.CTkButton] = {}
        self.preset_buttons: dict[int, ctk.CTkButton] = {}

        self.total_files = 0
        self.scanned_count = 0
        self.success_count = 0
        self.error_count = 0
        self.skipped_count = 0
        self.background_color = str(self.saved_settings.get("background_color", "#FFFFFF"))

        # Widgets that live in lazily built tabs.
        self.quality_slider: ctk.CTkSlider | None = None
        self.quality_value_label: ctk.CTkLabel | None = None
        self.quality_hint: ctk.CTkLabel | None = None
        self.bg_button: ctk.CTkButton | None = None
        self.bg_card: ctk.CTkFrame | None = None
        self.bg_hint: ctk.CTkLabel | None = None
        self.format_note: ctk.CTkLabel | None = None
        self.metadata_seg: ctk.CTkSegmentedButton | None = None
        self.structure_switch: ctk.CTkSwitch | None = None
        self.resize_switch: ctk.CTkSwitch | None = None
        self.width_entry: ctk.CTkEntry | None = None
        self.height_entry: ctk.CTkEntry | None = None
        self.upscale_switch: ctk.CTkSwitch | None = None
        self.resize_hint: ctk.CTkLabel | None = None
        self.input_path_label: ctk.CTkLabel | None = None
        self.output_path_label: ctk.CTkLabel | None = None
        self.scan_badge: ctk.CTkLabel | None = None
        self.summary_main: ctk.CTkLabel | None = None
        self.log_scroll: SlimScrollbar | None = None
        self.toast: Toast | None = None
        self.status_label: ctk.CTkLabel | None = None
        self.progress_bar: ctk.CTkProgressBar | None = None
        self.progress_percent: ctk.CTkLabel | None = None
        self.progress_detail: ctk.CTkLabel | None = None
        self.progress_meta: ctk.CTkLabel | None = None
        self.stat_total: ctk.CTkLabel | None = None
        self.stat_success: ctk.CTkLabel | None = None
        self.stat_skipped: ctk.CTkLabel | None = None
        self.stat_error: ctk.CTkLabel | None = None

        self.worker_thread: threading.Thread | None = None
        self.cancel_event = threading.Event()
        self.ui_queue: queue.Queue = queue.Queue()

        self.ui_font_family = self._resolve_ui_font()
        self._configure_tk_default_fonts()
        self._load_logo()

        default_input = str(Path.cwd() / "images")
        default_output = str(Path.cwd() / "converted_images")
        self.input_var = ctk.StringVar(value=self.saved_settings.get("input_folder") or default_input)
        self.output_var = ctk.StringVar(value=self.saved_settings.get("output_folder") or default_output)

        self.formats = available_output_formats()
        saved_format = str(self.saved_settings.get("output_format", "WEBP"))
        default_format = "WEBP" if "WEBP" in self.formats else self.formats[0]
        self.output_format_var = ctk.StringVar(
            value=saved_format if saved_format in self.formats else default_format
        )
        self.quality_var = ctk.IntVar(value=int(self.saved_settings.get("quality", 90)))
        self.include_subfolders_var = ctk.BooleanVar(
            value=bool(self.saved_settings.get("include_subfolders", True))
        )
        self.preserve_structure_var = ctk.BooleanVar(
            value=bool(self.saved_settings.get("preserve_structure", True))
        )
        self.overwrite_var = ctk.BooleanVar(value=bool(self.saved_settings.get("overwrite", False)))
        self.preserve_timestamps_var = ctk.BooleanVar(
            value=bool(self.saved_settings.get("preserve_timestamps", True))
        )
        self.preserve_animation_var = ctk.BooleanVar(
            value=bool(self.saved_settings.get("preserve_animation", True))
        )
        self.resize_enabled_var = ctk.BooleanVar(
            value=bool(self.saved_settings.get("resize_enabled", False))
        )
        self.allow_upscale_var = ctk.BooleanVar(
            value=bool(self.saved_settings.get("allow_upscale", False))
        )
        self.max_width_var = ctk.StringVar(value=str(self.saved_settings.get("max_width", "1920")))
        self.max_height_var = ctk.StringVar(value=str(self.saved_settings.get("max_height", "")))
        self.metadata_mode_var = ctk.StringVar(
            value="حذف کامل" if bool(self.saved_settings.get("strip_metadata", False)) else "حفظ"
        )

        self._theme_name = str(self.saved_settings.get("theme", "تیره"))
        if self._theme_name not in THEME_NAMES:
            self._theme_name = "تیره"
        ctk.set_appearance_mode(THEME_MODES[self._theme_name])

        self._restore_geometry()
        self._build_ui()
        self._install_settings_tracking()
        self._install_shortcuts()
        self._refresh_path_labels()
        self._update_summary()

        saved_tab = str(self.saved_settings.get("settings_tab", "خروجی"))
        if saved_tab not in TAB_NAMES:
            saved_tab = "خروجی"
        self.tab_bar.set(saved_tab)
        self._switch_tab(saved_tab, persist=False)

        self.theme_button.set(self._theme_name)
        self._on_format_changed(self.output_format_var.get())
        self._toggle_resize()
        self._sync_structure_state()
        self._render_presets()
        self._apply_background_button_style()

        self.after(30, self._apply_app_icon)
        self.after(120, self._schedule_scan)
        self._poll_job = self.after(90, self._poll_queue)

    # ------------------------------------------------------------------
    # Fonts, icon, geometry
    # ------------------------------------------------------------------

    def _resolve_ui_font(self) -> str:
        """Prefer Vazirmatn and safely fall back when it is not installed."""
        installed = {name.casefold(): name for name in tkfont.families(self)}
        for candidate in ("Vazirmatn", "Vazir Matn", "Vazir"):
            matched = installed.get(candidate.casefold())
            if matched:
                return matched
        return "Tahoma"

    def app_font(self, size: int, weight: str = "normal") -> ctk.CTkFont:
        """Return a cached font so hundreds of widgets share one object."""
        key = (size, weight)
        cached = self._font_cache.get(key)
        if cached is None:
            cached = ctk.CTkFont(family=self.ui_font_family, size=size, weight=weight)
            self._font_cache[key] = cached
        return cached

    _font = app_font

    def _configure_tk_default_fonts(self) -> None:
        """Apply the chosen family to Tk menus, dialogs and classic widgets."""
        for font_name in (
            "TkDefaultFont",
            "TkTextFont",
            "TkFixedFont",
            "TkMenuFont",
            "TkHeadingFont",
            "TkCaptionFont",
            "TkSmallCaptionFont",
            "TkIconFont",
            "TkTooltipFont",
        ):
            try:
                tkfont.nametofont(font_name).configure(family=self.ui_font_family)
            except Exception:
                pass

        self.option_add("*Font", f"{{{self.ui_font_family}}} 10")

    def _load_logo(self) -> None:
        try:
            image = Image.open(resource_path("assets/app_icon.png"))
            image.load()
            self._logo_source = image
        except Exception:
            self._logo_source = None

    def _logo_image(self) -> ctk.CTkImage | None:
        if self._logo_source is None:
            return None
        if self._logo_ctk is None:
            self._logo_ctk = ctk.CTkImage(
                light_image=self._logo_source,
                dark_image=self._logo_source,
                size=(34, 34),
            )
        return self._logo_ctk

    def _apply_app_icon(self) -> None:
        """Apply the same bundled icon to the window and the Windows taskbar."""
        png_path = resource_path("assets/app_icon.png")
        ico_path = resource_path("assets/app_icon.ico")

        try:
            if png_path.exists():
                self._icon_photo = tk.PhotoImage(file=str(png_path))
                self.iconphoto(True, self._icon_photo)
        except Exception:
            pass

        if sys.platform.startswith("win"):
            try:
                import ctypes

                ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID(
                    "ProImageConverter.Desktop.1.3"
                )
            except Exception:
                pass

            try:
                if ico_path.exists():
                    self.iconbitmap(str(ico_path))
            except Exception:
                pass

    def _restore_geometry(self) -> None:
        geometry = str(self.saved_settings.get("window_geometry") or "")
        if re.fullmatch(r"\d{3,5}x\d{3,5}[+-]-?\d+[+-]-?\d+", geometry):
            try:
                self.geometry(geometry)
                return
            except Exception:
                pass

        screen_width = self.winfo_screenwidth()
        screen_height = self.winfo_screenheight()
        width = min(1260, max(1060, screen_width - 80))
        height = min(840, max(660, screen_height - 100))
        x = max(0, (screen_width - width) // 2)
        y = max(0, (screen_height - height) // 2 - 24)
        self.geometry(f"{width}x{height}+{x}+{y}")

    # ------------------------------------------------------------------
    # UI construction
    # ------------------------------------------------------------------

    def _card(self, parent, **kwargs) -> ctk.CTkFrame:
        return ctk.CTkFrame(
            parent,
            fg_color=PALETTE["surface"],
            corner_radius=14,
            border_width=1,
            border_color=PALETTE["border"],
            **kwargs,
        )

    def _build_ui(self) -> None:
        self.grid_columnconfigure(0, weight=1)
        self.grid_columnconfigure(1, weight=0, minsize=self.SIDEBAR_WIDTH)
        self.grid_rowconfigure(1, weight=1)

        self._build_header()
        self._build_sidebar()
        self._build_workspace()

    # -- header ---------------------------------------------------------

    def _build_header(self) -> None:
        header = ctk.CTkFrame(self, fg_color=PALETTE["header"], corner_radius=0, height=74)
        header.grid(row=0, column=0, columnspan=2, sticky="ew")
        header.grid_columnconfigure(1, weight=1)
        header.grid_propagate(False)

        brand = ctk.CTkFrame(header, fg_color="transparent")
        brand.grid(row=0, column=2, sticky="e", padx=(20, 24), pady=12)
        brand.grid_columnconfigure(0, weight=1)

        logo = self._logo_image()
        self.brand_icon = ctk.CTkLabel(
            brand,
            text="" if logo else "◈",
            image=logo,
            width=34,
            font=self._font(22, "bold"),
            text_color=PALETTE["accent"],
        )
        self.brand_icon.grid(row=0, column=1, rowspan=2, padx=(0, 20))

        title_row = ctk.CTkFrame(brand, fg_color="transparent")
        title_row.grid(row=0, column=0, sticky="e")
        ctk.CTkLabel(
            title_row,
            text="Pro Image Converter",
            font=self._font(17, "bold"),
            text_color=PALETTE["text"],
        ).pack(side="right")
        ctk.CTkLabel(
            title_row,
            text=f"v{APP_VERSION}",
            font=self._font(9, "bold"),
            text_color=PALETTE["accent"],
            fg_color=PALETTE["accent_soft"],
            corner_radius=6,
            width=42,
            height=18,
        ).pack(side="right", padx=(8, 0))

        ctk.CTkLabel(
            brand,
            text="تبدیل گروهی تصاویر؛ سریع، امن و بدون معطلی",
            font=self._font(11),
            text_color=PALETTE["muted"],
        ).grid(row=1, column=0, sticky="e", pady=(1, 0))

        self.theme_button = ctk.CTkSegmentedButton(
            header,
            values=["سیستم", "روشن", "تیره"],
            command=self._change_theme,
            width=198,
            height=30,
            corner_radius=9,
            border_width=1,
            fg_color=PALETTE["surface_muted"],
            selected_color=PALETTE["accent"],
            selected_hover_color=PALETTE["accent_hover"],
            unselected_color=PALETTE["surface_muted"],
            unselected_hover_color=PALETTE["surface_hover"],
            text_color=PALETTE["text"],
            font=self._font(10),
            dynamic_resizing=False,
        )
        self.theme_button.grid(row=0, column=0, sticky="w", padx=24)

        ctk.CTkFrame(header, fg_color=PALETTE["border"], height=1, corner_radius=0).place(
            relx=0, rely=1, anchor="sw", relwidth=1
        )

    # -- sidebar --------------------------------------------------------

    def _build_sidebar(self) -> None:
        sidebar = ctk.CTkFrame(self, fg_color=PALETTE["sidebar"], corner_radius=0)
        sidebar.grid(row=1, column=1, sticky="nsew")
        sidebar.grid_columnconfigure(0, weight=1)
        sidebar.grid_rowconfigure(2, weight=1)

        ctk.CTkFrame(sidebar, fg_color=PALETTE["border"], width=1, corner_radius=0).place(
            x=0, y=0, relheight=1
        )

        head = ctk.CTkFrame(sidebar, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=20, pady=(18, 10))

        ctk.CTkLabel(
            head,
            text="تنظیمات تبدیل",
            font=self._font(14, "bold"),
            text_color=PALETTE["text"],
        ).pack(side="right")
        ctk.CTkLabel(
            head,
            text="ذخیره خودکار",
            font=self._font(10),
            text_color=PALETTE["faint"],
        ).pack(side="right", padx=(0, 8))

        self.tab_bar = ctk.CTkSegmentedButton(
            sidebar,
            values=["اندازه", "پردازش", "خروجی"],
            command=self._switch_tab,
            height=34,
            corner_radius=9,
            border_width=1,
            fg_color=PALETTE["surface_muted"],
            selected_color=PALETTE["accent"],
            selected_hover_color=PALETTE["accent_hover"],
            unselected_color=PALETTE["surface_muted"],
            unselected_hover_color=PALETTE["surface_hover"],
            text_color=PALETTE["text"],
            font=self._font(11, "bold"),
        )
        self.tab_bar.grid(row=1, column=0, sticky="ew", padx=20)

        self.tab_container = ctk.CTkFrame(sidebar, fg_color="transparent")
        self.tab_container.grid(row=2, column=0, sticky="nsew", padx=20, pady=(14, 0))
        self.tab_container.grid_columnconfigure(0, weight=1)
        self.tab_container.grid_rowconfigure(0, weight=1)

        for name in TAB_NAMES:
            self.tab_frames[name] = SettingsPane(self, self.tab_container)

        self.tab_builders = {
            "خروجی": self._build_output_tab,
            "پردازش": self._build_process_tab,
            "اندازه": self._build_size_tab,
        }

        # The output tab is the primary one, so it is always ready.
        self._ensure_tab("خروجی")

        summary = ctk.CTkFrame(
            sidebar,
            fg_color=PALETTE["surface_muted"],
            corner_radius=12,
        )
        summary.grid(row=3, column=0, sticky="ew", padx=20, pady=(12, 0))
        self.summary_main = ctk.CTkLabel(
            summary,
            text="",
            font=self._font(10),
            text_color=PALETTE["muted"],
            anchor="e",
            justify="right",
            wraplength=320,
        )
        self.summary_main.pack(fill="x", padx=12, pady=9)

        footer = ctk.CTkFrame(sidebar, fg_color="transparent")
        footer.grid(row=4, column=0, sticky="ew", padx=20, pady=(8, 12))
        capabilities = "   ".join(
            [
                "Pillow ✓",
                f"HEIC {'✓' if HEIF_ENABLED else '—'}",
                f"SVG {'✓' if SVG_ENABLED else '—'}",
                f"RAW {'✓' if RAW_ENABLED else '—'}",
            ]
        )
        ctk.CTkLabel(
            footer,
            text=capabilities,
            font=self._font(10),
            text_color=PALETTE["faint"],
        ).pack(anchor="e")

    def _ensure_tab(self, name: str) -> None:
        if name in self._built_tabs:
            return
        pane = self.tab_frames.get(name)
        builder = self.tab_builders.get(name)
        if pane is None or builder is None:
            return
        builder(pane.inner)
        pane.bind_wheel()
        pane._schedule_refresh()
        self._built_tabs.add(name)

    def _switch_tab(self, name: str, persist: bool = True) -> None:
        if name not in self.tab_frames:
            name = "خروجی"
        self._ensure_tab(name)
        self.tab_frames[name].outer.tkraise()
        if persist:
            self._schedule_settings_save()

    def _section_title(self, parent, text: str) -> ctk.CTkLabel:
        return ctk.CTkLabel(
            parent,
            text=text,
            font=self._font(11, "bold"),
            text_color=PALETTE["text"],
            anchor="e",
        )

    def _settings_card(self, parent, title: str) -> ctk.CTkFrame:
        card = self._card(parent)
        card.pack(fill="x", pady=(0, 10))
        head = ctk.CTkFrame(card, fg_color="transparent")
        head.pack(fill="x", padx=14, pady=(12, 2))
        ctk.CTkLabel(
            head,
            text=title,
            font=self._font(11, "bold"),
            text_color=PALETTE["text"],
        ).pack(side="right")
        return card

    def _switch_row(
        self,
        parent,
        title: str,
        variable,
        command: Callable | None = None,
        subtitle: str | None = None,
        pady: tuple[int, int] = (6, 6),
    ) -> tuple[ctk.CTkSwitch, ctk.CTkLabel]:
        row = ctk.CTkFrame(parent, fg_color="transparent")
        row.pack(fill="x", padx=14, pady=pady)

        switch = ctk.CTkSwitch(
            row,
            text="",
            variable=variable,
            command=command,
            width=44,
            switch_width=38,
            switch_height=20,
            progress_color=PALETTE["accent"],
            button_color=PALETTE["white"],
            button_hover_color=PALETTE["white"],
            fg_color=PALETTE["border"],
        )
        switch.pack(side="left")

        text_box = ctk.CTkFrame(row, fg_color="transparent")
        text_box.pack(side="right", fill="x", expand=True, padx=(10, 0))

        label = ctk.CTkLabel(
            text_box,
            text=title,
            font=self._font(11),
            text_color=PALETTE["text"],
            anchor="e",
            justify="right",
            wraplength=250,
        )
        label.pack(anchor="e")

        if subtitle:
            ctk.CTkLabel(
                text_box,
                text=subtitle,
                font=self._font(10),
                text_color=PALETTE["muted"],
                anchor="e",
                justify="right",
                wraplength=250,
            ).pack(anchor="e", pady=(1, 0))

        return switch, label

    # -- output tab -----------------------------------------------------

    def _build_output_tab(self, parent: ctk.CTkFrame) -> None:
        parent.grid_columnconfigure(0, weight=1)
        parent.grid_rowconfigure(5, weight=1)

        self._section_title(parent, "فرمت خروجی").grid(row=0, column=0, sticky="ew")

        format_grid = ctk.CTkFrame(parent, fg_color="transparent")
        format_grid.grid(row=1, column=0, sticky="ew", pady=(8, 0))
        columns = 5
        for column in range(columns):
            format_grid.grid_columnconfigure(column, weight=1, uniform="format")

        self.format_buttons = {}
        for index, name in enumerate(self.formats):
            row, raw_column = divmod(index, columns)
            column = columns - 1 - raw_column  # fill right-to-left
            button = ctk.CTkButton(
                format_grid,
                text=name,
                width=58,
                height=32,
                corner_radius=8,
                border_width=1,
                font=self._font(10, "bold"),
                command=lambda value=name: self._select_format(value),
            )
            button.grid(row=row, column=column, sticky="ew", padx=2, pady=3)
            self.format_buttons[name] = button

        self.format_note = ctk.CTkLabel(
            parent,
            text="",
            font=self._font(10),
            text_color=PALETTE["muted"],
            anchor="e",
            justify="right",
            wraplength=330,
        )
        self.format_note.grid(row=2, column=0, sticky="ew", pady=(8, 0))

        quality_card = self._card(parent)
        quality_card.grid(row=3, column=0, sticky="ew", pady=(14, 0))
        quality_head = ctk.CTkFrame(quality_card, fg_color="transparent")
        quality_head.pack(fill="x", padx=14, pady=(12, 0))
        ctk.CTkLabel(
            quality_head,
            text="کیفیت / فشرده‌سازی",
            font=self._font(11),
            text_color=PALETTE["text"],
        ).pack(side="right")
        self.quality_value_label = ctk.CTkLabel(
            quality_head,
            text="",
            font=self._font(13, "bold"),
            text_color=PALETTE["accent"],
        )
        self.quality_value_label.pack(side="left")

        self.quality_slider = ctk.CTkSlider(
            quality_card,
            from_=1,
            to=100,
            number_of_steps=99,
            variable=self.quality_var,
            command=self._quality_changed,
            height=16,
            progress_color=PALETTE["accent"],
            button_color=PALETTE["white"],
            button_hover_color=PALETTE["white"],
            fg_color=PALETTE["border"],
        )
        self.quality_slider.pack(fill="x", padx=14, pady=(8, 2))

        self.quality_hint = ctk.CTkLabel(
            quality_card,
            text="",
            font=self._font(10),
            text_color=PALETTE["muted"],
            anchor="e",
            justify="right",
            wraplength=320,
        )
        self.quality_hint.pack(fill="x", padx=14, pady=(0, 12))

        bg_card = self._card(parent)
        bg_card.grid(row=4, column=0, sticky="ew", pady=(10, 0))
        self.bg_card = bg_card
        bg_row = ctk.CTkFrame(bg_card, fg_color="transparent")
        bg_row.pack(fill="x", padx=14, pady=12)

        bg_text = ctk.CTkFrame(bg_row, fg_color="transparent")
        bg_text.pack(side="right", fill="x", expand=True)
        ctk.CTkLabel(
            bg_text,
            text="رنگ جایگزین شفافیت",
            font=self._font(11),
            text_color=PALETTE["text"],
            anchor="e",
        ).pack(anchor="e")
        self.bg_hint = ctk.CTkLabel(
            bg_text,
            text="",
            font=self._font(10),
            text_color=PALETTE["muted"],
            anchor="e",
            justify="right",
            wraplength=210,
        )
        self.bg_hint.pack(anchor="e", pady=(1, 0))

        self.bg_button = ctk.CTkButton(
            bg_row,
            text=self.background_color,
            width=88,
            height=34,
            corner_radius=8,
            command=self._choose_background_color,
            font=self._font(10, "bold"),
        )
        self.bg_button.pack(side="left", padx=(10, 0))

    # -- processing tab -------------------------------------------------

    def _build_process_tab(self, parent: ctk.CTkFrame) -> None:
        card = self._settings_card(parent, "ساختار پوشه‌ها")
        self._switch_row(
            card,
            "پردازش زیرپوشه‌ها",
            self.include_subfolders_var,
            self._on_include_subfolders_changed,
            pady=(6, 6),
        )
        self.structure_switch, _ = self._switch_row(
            card,
            "حفظ ساختار پوشه‌ای",
            self.preserve_structure_var,
            subtitle="پوشه‌های خروجی مانند ورودی ساخته می‌شوند.",
            pady=(2, 12),
        )

        card = self._settings_card(parent, "متادیتا (EXIF)")
        self.metadata_seg = ctk.CTkSegmentedButton(
            card,
            values=["حذف کامل", "حفظ"],
            variable=self.metadata_mode_var,
            command=self._on_metadata_mode_changed,
            height=32,
            corner_radius=8,
            border_width=1,
            fg_color=PALETTE["surface_muted"],
            selected_color=PALETTE["accent"],
            selected_hover_color=PALETTE["accent_hover"],
            unselected_color=PALETTE["surface_muted"],
            unselected_hover_color=PALETTE["surface_hover"],
            text_color=PALETTE["text"],
            font=self._font(10, "bold"),
        )
        self.metadata_seg.pack(fill="x", padx=14, pady=(4, 0))
        ctk.CTkLabel(
            card,
            text="«حذف کامل» پس از اصلاح چرخش، EXIF/XMP و پروفایل رنگ را پاک می‌کند.",
            font=self._font(10),
            text_color=PALETTE["muted"],
            anchor="e",
            justify="right",
            wraplength=320,
        ).pack(fill="x", padx=14, pady=(4, 8))

        self._switch_row(
            card,
            "حفظ تاریخ فایل‌ها",
            self.preserve_timestamps_var,
            pady=(2, 12),
        )

        card = self._settings_card(parent, "رفتار تبدیل")
        self._switch_row(
            card,
            "حفظ انیمیشن",
            self.preserve_animation_var,
            subtitle="برای فرمت‌های سازگار مانند WEBP و GIF.",
            pady=(6, 4),
        )
        self._switch_row(
            card,
            "بازنویسی فایل‌های موجود",
            self.overwrite_var,
            subtitle="اگر خاموش باشد، فایل‌های قبلی رد می‌شوند.",
            pady=(2, 12),
        )

        self._sync_structure_state()

    # -- resize tab -----------------------------------------------------

    def _build_size_tab(self, parent: ctk.CTkFrame) -> None:
        card = self._settings_card(parent, "تغییر اندازه")
        self.resize_switch, _ = self._switch_row(
            card,
            "فعال‌سازی تغییر اندازه",
            self.resize_enabled_var,
            self._toggle_resize,
            subtitle="ابعاد بزرگ‌تر متناسب با نسبت تصویر کوچک می‌شوند.",
            pady=(6, 4),
        )

        ctk.CTkLabel(
            card,
            text="عرض‌های آماده",
            font=self._font(10),
            text_color=PALETTE["muted"],
            anchor="e",
        ).pack(fill="x", padx=14, pady=(8, 2))

        preset_row = ctk.CTkFrame(card, fg_color="transparent")
        preset_row.pack(fill="x", padx=14)
        self.preset_buttons = {}
        for value in (1920, 1280, 800, 640):
            button = ctk.CTkButton(
                preset_row,
                text=to_fa(value),
                width=58,
                height=28,
                corner_radius=8,
                border_width=1,
                font=self._font(10),
                command=lambda preset=value: self._apply_size_preset(preset),
            )
            button.pack(side="right", padx=(4, 0))
            self.preset_buttons[value] = button

        fields = ctk.CTkFrame(card, fg_color="transparent")
        fields.pack(fill="x", padx=14, pady=(14, 0))
        fields.grid_columnconfigure((0, 1), weight=1, uniform="dimension")

        ctk.CTkLabel(
            fields,
            text="عرض (پیکسل)",
            font=self._font(10),
            text_color=PALETTE["muted"],
            anchor="e",
        ).grid(row=0, column=1, sticky="ew", padx=(6, 0))
        ctk.CTkLabel(
            fields,
            text="ارتفاع (پیکسل)",
            font=self._font(10),
            text_color=PALETTE["muted"],
            anchor="w",
        ).grid(row=0, column=0, sticky="ew", padx=(0, 6))

        self.width_entry = ctk.CTkEntry(
            fields,
            textvariable=self.max_width_var,
            justify="center",
            placeholder_text="خودکار",
            height=34,
            corner_radius=8,
            font=self._font(11),
        )
        self.width_entry.grid(row=1, column=1, sticky="ew", padx=(6, 0), pady=(5, 0))

        self.height_entry = ctk.CTkEntry(
            fields,
            textvariable=self.max_height_var,
            justify="center",
            placeholder_text="خودکار",
            height=34,
            corner_radius=8,
            font=self._font(11),
        )
        self.height_entry.grid(row=1, column=0, sticky="ew", padx=(0, 6), pady=(5, 0))

        self.resize_hint = ctk.CTkLabel(
            card,
            text="یک مقدار را خالی بگذارید تا بُعد دیگر خودکار محاسبه شود.",
            font=self._font(10),
            text_color=PALETTE["muted"],
            anchor="e",
            justify="right",
            wraplength=320,
        )
        self.resize_hint.pack(fill="x", padx=14, pady=(6, 8))

        self.upscale_switch, _ = self._switch_row(
            card,
            "اجازه بزرگ‌کردن تصاویر کوچک",
            self.allow_upscale_var,
            pady=(2, 12),
        )

        self._toggle_resize()
        self._render_presets()

    # -- workspace ------------------------------------------------------

    def _build_workspace(self) -> None:
        ws = ctk.CTkFrame(self, fg_color="transparent")
        ws.grid(row=1, column=0, sticky="nsew", padx=(22, 18), pady=(16, 16))
        ws.grid_columnconfigure(0, weight=1)
        ws.grid_rowconfigure(3, weight=1)
        self.workspace = ws

        self._build_path_card(ws)
        self._build_stats(ws)
        self._build_progress(ws)
        self._build_log(ws)
        self._build_actions(ws)

        self.toast = Toast(self)

    def _build_path_card(self, parent) -> None:
        card = self._card(parent)
        card.grid(row=0, column=0, sticky="ew")
        card.grid_columnconfigure(0, weight=1, uniform="path")
        card.grid_columnconfigure(2, weight=1, uniform="path")

        self.output_path_label = self._path_tile(
            card,
            0,
            "پوشه خروجی",
            self.output_var,
            self._choose_output_folder,
            open_folder=True,
        )
        ctk.CTkLabel(
            card,
            text="←",
            font=self._font(18),
            text_color=PALETTE["faint"],
        ).grid(row=0, column=1, padx=2)

        self.input_path_label = self._path_tile(
            card,
            2,
            "پوشه ورودی",
            self.input_var,
            self._choose_input_folder,
            with_refresh=True,
            with_badge=True,
        )

    def _path_tile(
        self,
        parent,
        column: int,
        title: str,
        variable,
        pick_command: Callable[[], None],
        *,
        with_refresh: bool = False,
        open_folder: bool = False,
        with_badge: bool = False,
    ) -> ctk.CTkLabel:
        tile = ctk.CTkFrame(parent, fg_color="transparent")
        tile.grid(row=0, column=column, sticky="nsew", padx=14, pady=12)

        head = ctk.CTkFrame(tile, fg_color="transparent")
        head.pack(fill="x")
        ctk.CTkLabel(
            head,
            text=title,
            font=self._font(11),
            text_color=PALETTE["muted"],
        ).pack(side="right")

        if with_badge:
            self.scan_badge = ctk.CTkLabel(
                head,
                text="—",
                font=self._font(9, "bold"),
                text_color=PALETTE["accent"],
                fg_color=PALETTE["accent_soft"],
                corner_radius=8,
                width=86,
                height=20,
            )
            self.scan_badge.pack(side="right", padx=(8, 0))

        label = ctk.CTkLabel(
            tile,
            text="—",
            font=self._font(11),
            text_color=PALETTE["text"],
            anchor="e",
            justify="right",
            cursor="hand2",
        )
        label.pack(fill="x", pady=(6, 10))
        label.bind("<Button-1>", lambda _event, target=variable: self._copy_path(target))

        buttons = ctk.CTkFrame(tile, fg_color="transparent")
        buttons.pack(fill="x")

        pick = ctk.CTkButton(
            buttons,
            text="انتخاب",
            width=70,
            height=30,
            corner_radius=8,
            border_width=1,
            fg_color="transparent",
            border_color=PALETTE["accent"],
            hover_color=PALETTE["accent_soft"],
            text_color=PALETTE["accent"],
            font=self._font(10, "bold"),
            command=pick_command,
        )
        pick.pack(side="right")

        if with_refresh:
            refresh = ctk.CTkButton(
                buttons,
                text="↻",
                width=30,
                height=30,
                corner_radius=8,
                fg_color=PALETTE["surface_muted"],
                hover_color=PALETTE["surface_hover"],
                text_color=PALETTE["muted"],
                font=self._font(13),
                command=self._schedule_scan,
            )
            refresh.pack(side="right", padx=(6, 0))

        if open_folder:
            open_button = ctk.CTkButton(
                buttons,
                text="باز کردن",
                width=84,
                height=28,
                corner_radius=8,
                fg_color="transparent",
                hover_color=PALETTE["surface_hover"],
                text_color=PALETTE["muted"],
                font=self._font(10),
                command=self._open_output_folder,
            )
            open_button.pack(side="left")

        return label

    def _build_stats(self, parent) -> None:
        stats = ctk.CTkFrame(parent, fg_color="transparent")
        stats.grid(row=1, column=0, sticky="ew", pady=(12, 0))
        for column in range(4):
            stats.grid_columnconfigure(column, weight=1, uniform="stat")

        self.stat_total = self._stat_card(stats, 3, "کل فایل‌ها", PALETTE["text"])
        self.stat_success = self._stat_card(stats, 2, "موفق", PALETTE["success"])
        self.stat_skipped = self._stat_card(stats, 1, "ردشده", PALETTE["skip"])
        self.stat_error = self._stat_card(stats, 0, "خطا", PALETTE["danger"])

    def _stat_card(self, parent, column: int, title: str, value_color) -> ctk.CTkLabel:
        card = self._card(parent)
        card.grid(row=0, column=column, sticky="ew", padx=4)

        row = ctk.CTkFrame(card, fg_color="transparent")
        row.pack(fill="x", padx=12, pady=10)
        ctk.CTkLabel(
            row,
            text=title,
            font=self._font(11),
            text_color=PALETTE["muted"],
            anchor="e",
        ).pack(side="right")
        ctk.CTkLabel(
            row,
            text="●",
            font=self._font(7),
            text_color=value_color,
        ).pack(side="right", padx=(0, 5))

        value = ctk.CTkLabel(
            row,
            text="۰",
            font=self._font(21, "bold"),
            text_color=value_color,
            anchor="w",
        )
        value.pack(side="left")
        return value

    def _build_progress(self, parent) -> None:
        card = self._card(parent)
        card.grid(row=2, column=0, sticky="ew", pady=(12, 0))

        head = ctk.CTkFrame(card, fg_color="transparent")
        head.pack(fill="x", padx=14, pady=(11, 6))

        self.status_label = ctk.CTkLabel(
            head,
            text="آماده برای تبدیل",
            font=self._font(11, "bold"),
            text_color=PALETTE["text"],
            anchor="e",
        )
        self.status_label.pack(side="right", fill="x", expand=True)

        self.progress_percent = ctk.CTkLabel(
            head,
            text="۰٪",
            font=self._font(13, "bold"),
            text_color=PALETTE["accent"],
        )
        self.progress_percent.pack(side="left", padx=(10, 0))

        self.progress_bar = ctk.CTkProgressBar(
            card,
            height=8,
            corner_radius=4,
            progress_color=PALETTE["accent"],
            fg_color=PALETTE["surface_muted"],
        )
        self.progress_bar.pack(fill="x", padx=14, pady=(0, 8))
        self.progress_bar.set(0)

        foot = ctk.CTkFrame(card, fg_color="transparent")
        foot.pack(fill="x", padx=14, pady=(0, 11))

        self.progress_detail = ctk.CTkLabel(
            foot,
            text="۰ از ۰ فایل",
            font=self._font(10),
            text_color=PALETTE["muted"],
            anchor="e",
        )
        self.progress_detail.pack(side="right")

        self.progress_meta = ctk.CTkLabel(
            foot,
            text="",
            font=self._font(10),
            text_color=PALETTE["muted"],
            anchor="w",
        )
        self.progress_meta.pack(side="left")

    def _build_log(self, parent) -> None:
        card = self._card(parent)
        card.grid(row=3, column=0, sticky="nsew", pady=(12, 0))
        card.grid_columnconfigure(0, weight=1)
        card.grid_rowconfigure(1, weight=1)

        head = ctk.CTkFrame(card, fg_color="transparent")
        head.grid(row=0, column=0, sticky="ew", padx=14, pady=(9, 5))
        head.grid_columnconfigure(0, weight=1)

        ctk.CTkLabel(
            head,
            text="Processing log",
            font=self._font(11, "bold"),
            text_color=PALETTE["text"],
        ).grid(row=0, column=1, sticky="e")

        clear = ctk.CTkButton(
            head,
            text="Clear",
            width=72,
            height=26,
            corner_radius=8,
            fg_color="transparent",
            border_width=1,
            border_color=PALETTE["border"],
            hover_color=PALETTE["surface_hover"],
            text_color=PALETTE["muted"],
            font=self._font(10),
            command=self._clear_log,
        )
        clear.grid(row=0, column=0, sticky="w")

        body = ctk.CTkFrame(card, fg_color="transparent")
        body.grid(row=1, column=0, sticky="nsew", padx=12, pady=(0, 12))
        body.grid_columnconfigure(1, weight=1)
        body.grid_rowconfigure(0, weight=1)

        # A classic Tk text widget is used on purpose: CustomTkinter's textbox
        # redraws its scrollbar about twenty times per second forever. For a
        # log panel that is pure overhead, so the scrollbar is added here.
        self.log_box = tk.Text(
            body,
            wrap="word",
            borderwidth=0,
            highlightthickness=1,
            highlightbackground=self._theme_color("border"),
            highlightcolor=self._theme_color("border"),
            relief="flat",
            padx=10,
            pady=8,
            font=(self.ui_font_family, 10),
            spacing1=1,
            spacing3=2,
            insertwidth=0,
            takefocus=0,
            cursor="arrow",
            bg=self._theme_color("surface_muted"),
            fg=self._theme_color("text"),
            selectbackground=self._theme_color("accent_soft"),
            selectforeground=self._theme_color("text"),
        )
        self.log_box.grid(row=0, column=1, sticky="nsew")

        self.log_scroll = SlimScrollbar(
            body,
            command=self.log_box.yview,
            width=8,
            background=self._theme_color("surface_muted"),
            thumb=self._theme_color("faint"),
            hover=self._theme_color("muted"),
        )
        self.log_scroll.grid(row=0, column=0, sticky="ns", padx=(0, 6))
        self.log_box.configure(yscrollcommand=self.log_scroll.set)

        self._configure_log_tags()
        self.log_box.insert(
            "end",
            "Ready.\n"
            "Shortcuts: Ctrl+Enter start | Esc cancel | Ctrl+O choose input folder\n",
            "meta",
        )
        self.log_box.configure(state="disabled")

    def _theme_color(self, key: str) -> str:
        value = PALETTE[key]
        if isinstance(value, tuple):
            return value[0] if ctk.get_appearance_mode().lower() == "light" else value[1]
        return value

    def _configure_log_tags(self) -> None:
        self.log_box.tag_configure("ok", foreground=self._theme_color("success"))
        self.log_box.tag_configure("skip", foreground=self._theme_color("muted"))
        self.log_box.tag_configure("error", foreground=self._theme_color("danger"))
        self.log_box.tag_configure("meta", foreground=self._theme_color("muted"))

    def _restyle_log(self) -> None:
        if getattr(self, "log_box", None) is None:
            return
        background = self._theme_color("surface_muted")
        foreground = self._theme_color("text")
        self.log_box.configure(
            bg=background,
            fg=foreground,
            insertbackground=foreground,
            selectbackground=self._theme_color("accent_soft"),
            selectforeground=foreground,
            highlightbackground=self._theme_color("border"),
            highlightcolor=self._theme_color("border"),
        )
        if getattr(self, "log_scroll", None) is not None:
            self.log_scroll.restyle(
                background,
                self._theme_color("faint"),
                self._theme_color("muted"),
            )
        self._configure_log_tags()

    def _build_actions(self, parent) -> None:
        actions = ctk.CTkFrame(parent, fg_color="transparent")
        actions.grid(row=4, column=0, sticky="ew", pady=(14, 0))
        actions.grid_columnconfigure(2, weight=1)

        self.start_button = ctk.CTkButton(
            actions,
            text="شروع تبدیل",
            width=186,
            height=46,
            corner_radius=12,
            font=self._font(13, "bold"),
            fg_color=PALETTE["accent"],
            hover_color=PALETTE["accent_hover"],
            command=self._start_conversion,
        )
        self.start_button.grid(row=0, column=0, sticky="w")

        self.cancel_button = ctk.CTkButton(
            actions,
            text="لغو",
            width=92,
            height=46,
            corner_radius=12,
            fg_color="transparent",
            border_width=1,
            border_color=PALETTE["danger"],
            hover_color=PALETTE["danger_soft"],
            text_color=PALETTE["danger"],
            text_color_disabled=PALETTE["faint"],
            font=self._font(11, "bold"),
            state="disabled",
            command=self._cancel_conversion,
        )
        self.cancel_button.grid(row=0, column=1, padx=(10, 0))
        self.cancel_button.grid_remove()

        self.open_output_button = ctk.CTkButton(
            actions,
            text="باز کردن پوشه خروجی",
            width=172,
            height=46,
            corner_radius=12,
            fg_color="transparent",
            border_width=1,
            border_color=PALETTE["border"],
            hover_color=PALETTE["surface_hover"],
            text_color=PALETTE["text"],
            font=self._font(11),
            command=self._open_output_folder,
        )
        self.open_output_button.grid(row=0, column=3, sticky="e")

    # ------------------------------------------------------------------
    # Settings persistence
    # ------------------------------------------------------------------

    def _install_shortcuts(self) -> None:
        self.bind_all("<Control-Return>", lambda _event: self._start_conversion())
        self.bind_all("<Control-KP_Enter>", lambda _event: self._start_conversion())
        self.bind_all("<Escape>", lambda _event: self._cancel_conversion())
        self.bind_all("<Control-o>", lambda _event: self._choose_input_folder())
        self.bind_all("<Control-O>", lambda _event: self._choose_output_folder())
        self.bind_all("<Control-l>", lambda _event: self._clear_log())

    def _install_settings_tracking(self) -> None:
        self.input_var.trace_add("write", self._on_paths_changed)
        self.output_var.trace_add("write", self._on_paths_changed)
        self.include_subfolders_var.trace_add("write", self._on_include_subfolders_changed)
        self.max_width_var.trace_add("write", self._on_width_changed)

        tracked = (
            self.input_var,
            self.output_var,
            self.output_format_var,
            self.quality_var,
            self.include_subfolders_var,
            self.preserve_structure_var,
            self.overwrite_var,
            self.metadata_mode_var,
            self.preserve_timestamps_var,
            self.preserve_animation_var,
            self.resize_enabled_var,
            self.allow_upscale_var,
            self.max_width_var,
            self.max_height_var,
        )
        for variable in tracked:
            variable.trace_add("write", self._schedule_settings_save)

        for variable in (
            self.output_format_var,
            self.quality_var,
            self.resize_enabled_var,
            self.max_width_var,
            self.max_height_var,
            self.metadata_mode_var,
            self.include_subfolders_var,
            self.preserve_structure_var,
        ):
            variable.trace_add("write", self._update_summary)

    def _collect_settings(self) -> dict:
        metadata_mode = self.metadata_mode_var.get()
        geometry = ""
        try:
            if self.state() == "normal" and self.winfo_width() > 200 and self.winfo_height() > 200:
                geometry = (
                    f"{self.winfo_width()}x{self.winfo_height()}"
                    f"+{self.winfo_x()}+{self.winfo_y()}"
                )
            else:
                geometry = self.geometry()
        except Exception:
            geometry = ""
        return {
            "input_folder": self.input_var.get().strip(),
            "output_folder": self.output_var.get().strip(),
            "output_format": self.output_format_var.get(),
            "quality": int(self.quality_var.get()),
            "include_subfolders": bool(self.include_subfolders_var.get()),
            "preserve_structure": bool(self.preserve_structure_var.get()),
            "overwrite": bool(self.overwrite_var.get()),
            "preserve_metadata": metadata_mode != "حذف کامل",
            "strip_metadata": metadata_mode == "حذف کامل",
            "preserve_timestamps": bool(self.preserve_timestamps_var.get()),
            "preserve_animation": bool(self.preserve_animation_var.get()),
            "resize_enabled": bool(self.resize_enabled_var.get()),
            "allow_upscale": bool(self.allow_upscale_var.get()),
            "max_width": self.max_width_var.get().strip(),
            "max_height": self.max_height_var.get().strip(),
            "background_color": self.background_color,
            "theme": self.theme_button.get(),
            "settings_tab": self.tab_bar.get(),
            "window_geometry": geometry,
        }

    def _schedule_settings_save(self, *_args) -> None:
        if self._settings_save_job is not None:
            try:
                self.after_cancel(self._settings_save_job)
            except Exception:
                pass
        self._settings_save_job = self.after(450, self._save_settings_now)

    def _save_settings_now(self) -> None:
        if self._settings_save_job is not None:
            try:
                self.after_cancel(self._settings_save_job)
            except Exception:
                pass
        self._settings_save_job = None
        try:
            save_settings(self._collect_settings())
        except OSError as exc:
            if hasattr(self, "log_box"):
                self._append_log(f"[SETTINGS] Could not save settings: {exc}")

    def _on_paths_changed(self, *_args) -> None:
        self._refresh_path_labels()
        if not self._run_active:
            self._reset_results()
        self._schedule_scan()

    def _on_include_subfolders_changed(self, *_args) -> None:
        self._sync_structure_state()
        self._schedule_scan()

    def _on_width_changed(self, *_args) -> None:
        self._render_presets()

    def _update_summary(self, *_args) -> None:
        if self.summary_main is None:
            return

        output_format = self.output_format_var.get()
        parts = [output_format]
        if output_format in LOSSY_FORMATS or output_format == "PNG":
            parts.append(f"کیفیت {to_fa(self.quality_var.get())}")

        if self.resize_enabled_var.get():
            width = self.max_width_var.get().strip()
            height = self.max_height_var.get().strip()
            if width and height:
                parts.append(f"اندازه {to_fa(width)}×{to_fa(height)}")
            elif width:
                parts.append(f"عرض {to_fa(width)}")
            elif height:
                parts.append(f"ارتفاع {to_fa(height)}")
            else:
                parts.append("اندازه خودکار")
        else:
            parts.append("اندازه اصلی")

        parts.append("حذف متادیتا" if self.metadata_mode_var.get() == "حذف کامل" else "حفظ متادیتا")
        self.summary_main.configure(text="  •  ".join(parts))

    def _refresh_path_labels(self) -> None:
        if self.input_path_label is not None:
            self.input_path_label.configure(
                text=short_path(self.input_var.get().strip() or "—", 40)
            )
        if self.output_path_label is not None:
            self.output_path_label.configure(
                text=short_path(self.output_var.get().strip() or "—", 40)
            )

    # ------------------------------------------------------------------
    # Folder scanning (off the UI thread)
    # ------------------------------------------------------------------

    def _schedule_scan(self, *_args) -> None:
        if self._run_active or self._closing:
            return
        if self._scan_after_job is not None:
            try:
                self.after_cancel(self._scan_after_job)
            except Exception:
                pass
        self._scan_after_job = self.after(450, self._start_scan)

    def _start_scan(self) -> None:
        self._scan_after_job = None
        if self._run_active or self._closing:
            return

        input_text = self.input_var.get().strip()
        if not input_text:
            self._set_scan_badge("—")
            return

        folder = Path(input_text).expanduser()
        if not folder.is_dir():
            self._set_scan_badge("مسیر نامعتبر")
            return

        output_text = self.output_var.get().strip()
        output = Path(output_text).expanduser() if output_text else folder
        options = ConversionOptions(
            input_folder=folder,
            output_folder=output,
            output_format=self.output_format_var.get() or "WEBP",
            include_subfolders=bool(self.include_subfolders_var.get()),
        )

        self._scan_generation += 1
        generation = self._scan_generation
        self._set_scan_badge("در حال شمارش…")
        threading.Thread(
            target=self._scan_worker,
            args=(generation, options),
            daemon=True,
        ).start()

    def _scan_worker(self, generation: int, options: ConversionOptions) -> None:
        try:
            count = len(BatchImageConverter(options).discover_files())
            self.ui_queue.put(("scan_done", generation, count, None))
        except Exception as exc:  # pragma: no cover - defensive
            self.ui_queue.put(("scan_done", generation, 0, exc))

    def _handle_scan_done(self, generation: int, count: int, error: Exception | None) -> None:
        if generation != self._scan_generation or self._run_active:
            return

        if error is not None:
            self._set_scan_badge("خطا")
            self._append_log_lines([f"[SCAN] Could not scan input files: {error}"])
            return

        self.scanned_count = count
        self._set_scan_badge(f"{to_fa(count)} فایل" if count else "فایلی نیست")

        if self.success_count == 0 and self.error_count == 0 and self.skipped_count == 0:
            self.total_files = count
            if self.stat_total is not None:
                self.stat_total.configure(text=to_fa(count))
            if self.status_label is not None:
                self.status_label.configure(
                    text=(
                        f"{to_fa(count)} تصویر آماده تبدیل است"
                        if count
                        else "در پوشه ورودی تصویری پیدا نشد"
                    )
                )

    def _set_scan_badge(self, text: str) -> None:
        if self.scan_badge is not None:
            self.scan_badge.configure(text=text)

    def _reset_results(self) -> None:
        self.total_files = 0
        self.success_count = 0
        self.error_count = 0
        self.skipped_count = 0
        self._last_progress = None

        for label in (self.stat_total, self.stat_success, self.stat_skipped, self.stat_error):
            if label is not None:
                label.configure(text="۰")
        if self.progress_bar is not None:
            self.progress_bar.set(0)
        if self.progress_percent is not None:
            self.progress_percent.configure(text="۰٪")
        if self.progress_detail is not None:
            self.progress_detail.configure(text="۰ از ۰ فایل")
        if self.progress_meta is not None:
            self.progress_meta.configure(text="")
        if self.status_label is not None:
            self.status_label.configure(text="آماده برای تبدیل")

    # ------------------------------------------------------------------
    # User actions
    # ------------------------------------------------------------------

    def _choose_input_folder(self) -> None:
        current = Path(self.input_var.get()).expanduser()
        selected = filedialog.askdirectory(
            title="انتخاب پوشه تصاویر",
            initialdir=str(current) if current.is_dir() else None,
        )
        if not selected:
            return

        previous_input = self.input_var.get().strip()
        self.input_var.set(selected)

        output_text = self.output_var.get().strip()
        output_path = Path(output_text) if output_text else None
        if output_path is None or not output_text or output_text.endswith("converted_images"):
            self.output_var.set(str(Path(selected) / "converted_images"))
        elif previous_input and output_text == str(Path(previous_input) / "converted_images"):
            self.output_var.set(str(Path(selected) / "converted_images"))

    def _choose_output_folder(self) -> None:
        current = Path(self.output_var.get()).expanduser()
        selected = filedialog.askdirectory(
            title="انتخاب پوشه خروجی",
            initialdir=str(current) if current.is_dir() else None,
        )
        if selected:
            self.output_var.set(selected)

    def _choose_background_color(self) -> None:
        chosen = colorchooser.askcolor(color=self.background_color, title="انتخاب رنگ پس‌زمینه")
        if chosen and chosen[1]:
            self.background_color = chosen[1].upper()
            self._apply_background_button_style()
            self._schedule_settings_save()

    def _apply_background_button_style(self) -> None:
        if self.bg_button is None:
            return
        try:
            rgb = ImageColor.getrgb(self.background_color)
        except ValueError:
            self.background_color = "#FFFFFF"
            rgb = (255, 255, 255)

        luminance = 0.299 * rgb[0] + 0.587 * rgb[1] + 0.114 * rgb[2]
        text_color = "#111111" if luminance > 150 else "#FFFFFF"
        self.bg_button.configure(
            text=self.background_color,
            fg_color=self.background_color,
            hover_color=self.background_color,
            text_color=text_color,
        )

    def _copy_path(self, variable) -> None:
        value = variable.get().strip()
        if not value or self.toast is None:
            return
        self.clipboard_clear()
        self.clipboard_append(value)
        self.toast.show("مسیر کپی شد", value, kind="info", duration=2400)

    def _change_theme(self, selected: str, persist: bool = True) -> None:
        ctk.set_appearance_mode(THEME_MODES.get(selected, "dark"))
        self._restyle_log()
        for pane in self.tab_frames.values():
            pane.restyle()
        if persist:
            self._schedule_settings_save()

    def _select_format(self, name: str) -> None:
        if self.output_format_var.get() == name:
            return
        self.output_format_var.set(name)
        self._on_format_changed(name)

    def _render_format_buttons(self) -> None:
        selected = self.output_format_var.get()
        for name, button in self.format_buttons.items():
            if name == selected:
                button.configure(
                    fg_color=PALETTE["accent"],
                    hover_color=PALETTE["accent_hover"],
                    text_color="#FFFFFF",
                    border_color=PALETTE["accent"],
                )
            else:
                button.configure(
                    fg_color=PALETTE["surface_muted"],
                    hover_color=PALETTE["surface_hover"],
                    text_color=PALETTE["text"],
                    border_color=PALETTE["border"],
                )

    def _on_format_changed(self, selected: str) -> None:
        self._render_format_buttons()

        if self.format_note is not None:
            self.format_note.configure(text=FORMAT_NOTES.get(selected, ""))

        if self.quality_slider is not None and self.quality_hint is not None:
            if selected in LOSSY_FORMATS:
                self.quality_slider.configure(state="normal")
                self.quality_hint.configure(text="مقدار کمتر = فایل سبک‌تر، مقدار بیشتر = کیفیت بالاتر")
            elif selected == "PNG":
                self.quality_slider.configure(state="normal")
                self.quality_hint.configure(text="برای PNG، عدد کمتر یعنی فشرده‌سازی بیشتر (بدون افت کیفیت)")
            else:
                self.quality_slider.configure(state="disabled")
                self.quality_hint.configure(text="این فرمت تنظیم کیفیت عددی ندارد")

        if self.quality_value_label is not None:
            self.quality_value_label.configure(text=to_fa(round(self.quality_var.get())))

        if self.bg_button is not None:
            supports_alpha = selected not in ALPHA_UNSUPPORTED
            if self.bg_card is not None:
                if supports_alpha:
                    self.bg_card.grid_remove()
                else:
                    self.bg_card.grid()
            self.bg_button.configure(state="normal")
            if self.bg_hint is not None and not supports_alpha:
                self.bg_hint.configure(text=f"شفافیت در {selected} با این رنگ پر می‌شود.")

    def _quality_changed(self, value: float) -> None:
        if self.quality_value_label is not None:
            self.quality_value_label.configure(text=to_fa(round(value)))

    def _on_metadata_mode_changed(self, _value: str) -> None:
        self._schedule_settings_save()

    def _toggle_resize(self, *_args) -> None:
        if self.width_entry is None:
            return
        state = "normal" if self.resize_enabled_var.get() else "disabled"
        for widget in (
            self.width_entry,
            self.height_entry,
            self.upscale_switch,
            self.resize_hint,
        ):
            if widget is not None:
                widget.configure(state=state)

    def _sync_structure_state(self, *_args) -> None:
        if self.structure_switch is None:
            return
        if self.include_subfolders_var.get():
            self.structure_switch.configure(state="normal")
        else:
            self.preserve_structure_var.set(False)
            self.structure_switch.configure(state="disabled")

    def _apply_size_preset(self, value: int) -> None:
        self.max_width_var.set(str(value))
        self.max_height_var.set("")
        if not self.resize_enabled_var.get():
            self.resize_enabled_var.set(True)
        self._toggle_resize()
        self._render_presets()

    def _render_presets(self, *_args) -> None:
        if not self.preset_buttons:
            return
        active_width = self.max_width_var.get().strip()
        for value, button in self.preset_buttons.items():
            if str(value) == active_width:
                button.configure(
                    fg_color=PALETTE["accent"],
                    hover_color=PALETTE["accent_hover"],
                    text_color="#FFFFFF",
                    border_color=PALETTE["accent"],
                )
            else:
                button.configure(
                    fg_color=PALETTE["surface_muted"],
                    hover_color=PALETTE["surface_hover"],
                    text_color=PALETTE["text"],
                    border_color=PALETTE["border"],
                )

    # ------------------------------------------------------------------
    # Validation
    # ------------------------------------------------------------------

    def _notify_error(self, title: str, message: str, duration: int = 5500) -> None:
        if self.toast is not None:
            self.toast.show(title, message, kind="error", duration=duration)

    def _validated_options(self) -> ConversionOptions | None:
        input_text = self.input_var.get().strip()
        output_text = self.output_var.get().strip()

        if not input_text:
            self._notify_error("پوشه ورودی انتخاب نشده", "با دکمه «انتخاب» پوشه تصاویر را مشخص کنید.")
            return None

        input_folder = Path(input_text).expanduser()
        if not input_folder.is_dir():
            self._notify_error(
                "پوشه ورودی پیدا نشد",
                "مسیر واردشده وجود ندارد یا قابل دسترسی نیست.",
            )
            return None

        if not output_text:
            self._notify_error("پوشه خروجی انتخاب نشده", "پوشه ذخیره فایل‌های خروجی را مشخص کنید.")
            return None

        output_folder = Path(output_text).expanduser()
        if output_folder.exists() and not output_folder.is_dir():
            self._notify_error("پوشه خروجی نامعتبر است", "مسیر خروجی یک فایل است، نه پوشه.")
            return None

        max_width: int | None = None
        max_height: int | None = None

        if self.resize_enabled_var.get():
            width_text = self.max_width_var.get().strip()
            height_text = self.max_height_var.get().strip()

            try:
                if width_text:
                    max_width = int(width_text)
                    if max_width <= 0:
                        raise ValueError
                if height_text:
                    max_height = int(height_text)
                    if max_height <= 0:
                        raise ValueError
            except ValueError:
                self._notify_error(
                    "اندازه نامعتبر",
                    "عرض و ارتفاع باید خالی یا یک عدد صحیح بزرگ‌تر از صفر باشند.",
                )
                return None

            if max_width is None and max_height is None:
                self._notify_error(
                    "اندازه وارد نشده",
                    "برای تغییر اندازه، حداقل عرض یا ارتفاع را وارد کنید.",
                )
                return None

        try:
            ImageColor.getrgb(self.background_color)
        except ValueError:
            self._notify_error("رنگ نامعتبر", "رنگ پس‌زمینه معتبر نیست.")
            return None

        metadata_mode = self.metadata_mode_var.get()
        return ConversionOptions(
            input_folder=input_folder,
            output_folder=output_folder,
            output_format=self.output_format_var.get(),
            quality=int(self.quality_var.get()),
            include_subfolders=bool(self.include_subfolders_var.get()),
            preserve_structure=bool(self.preserve_structure_var.get()),
            overwrite=bool(self.overwrite_var.get()),
            preserve_metadata=metadata_mode != "حذف کامل",
            strip_metadata=metadata_mode == "حذف کامل",
            preserve_timestamps=bool(self.preserve_timestamps_var.get()),
            preserve_animation=bool(self.preserve_animation_var.get()),
            resize_enabled=bool(self.resize_enabled_var.get()),
            max_width=max_width,
            max_height=max_height,
            allow_upscale=bool(self.allow_upscale_var.get()),
            background_color=self.background_color,
        )

    # ------------------------------------------------------------------
    # Conversion
    # ------------------------------------------------------------------

    def _start_conversion(self) -> None:
        if self._run_active or (self.worker_thread and self.worker_thread.is_alive()):
            return

        options = self._validated_options()
        if options is None:
            return

        self._save_settings_now()
        self._scan_generation += 1  # ignore results of an in-flight scan
        if self.toast is not None:
            self.toast.hide()

        self._reset_results()
        self.total_files = self.scanned_count
        if self.stat_total is not None:
            self.stat_total.configure(text=to_fa(self.scanned_count))
        if self.progress_detail is not None:
            self.progress_detail.configure(text="در حال شمارش فایل‌ها…")
        if self.status_label is not None:
            self.status_label.configure(text="در حال آماده‌سازی فهرست فایل‌ها…")
        self._pending_log.clear()
        self._run_active = True
        self._run_started_at = time.perf_counter()
        self.cancel_event.clear()

        self._append_log_lines(
            [
                f"[START] {options.output_format} • quality={options.quality}",
                f"[INPUT] {options.input_folder}",
                f"[OUTPUT] {options.output_folder}",
            ]
        )

        self.start_button.configure(state="disabled")
        self.cancel_button.grid()
        self.cancel_button.configure(state="normal")

        self.worker_thread = threading.Thread(
            target=self._conversion_worker,
            args=(options,),
            daemon=True,
        )
        self.worker_thread.start()

    def _conversion_worker(self, options: ConversionOptions) -> None:
        started = time.perf_counter()
        try:
            engine = BatchImageConverter(
                options=options,
                progress_callback=self._queue_progress,
                cancel_event=self.cancel_event,
            )
            files = engine.discover_files()
            self.ui_queue.put(("scanned", len(files)))
            results = engine.run(files)
            self.ui_queue.put(
                ("done", results, self.cancel_event.is_set(), time.perf_counter() - started)
            )
        except Exception as exc:  # pragma: no cover - defensive
            self.ui_queue.put(("fatal", exc))

    def _queue_progress(self, index: int, total: int, result: ConversionResult) -> None:
        self.ui_queue.put(("progress", index, total, result))

    def _cancel_conversion(self) -> None:
        if self._run_active:
            self.cancel_event.set()
            self.cancel_button.configure(state="disabled")
            if self.status_label is not None:
                self.status_label.configure(text="در حال توقف امن پردازش…")
            self._append_log_lines(["[CANCEL] Cancellation requested by user."])

    # ------------------------------------------------------------------
    # Queue polling and rendering
    # ------------------------------------------------------------------

    def _poll_queue(self) -> None:
        self._poll_job = None
        drained = False
        try:
            while True:
                event = self.ui_queue.get_nowait()
                drained = True
                self._handle_event(event)
        except queue.Empty:
            pass

        if drained:
            self._flush_progress()
            self._flush_log()

        if self._pending_done is not None:
            payload = self._pending_done
            self._pending_done = None
            self._finish_run(*payload)

        if self._pending_fatal is not None:
            error = self._pending_fatal
            self._pending_fatal = None
            self._handle_fatal(error)

        if self._closing:
            return

        interval = 90 if self._run_active else 340
        self._poll_job = self.after(interval, self._poll_queue)

    def _handle_event(self, event: tuple) -> None:
        kind = event[0]

        if kind == "progress":
            _, index, total, result = event
            self.total_files = total
            if result.success:
                self.success_count += 1
            elif result.skipped:
                self.skipped_count += 1
            else:
                self.error_count += 1
            self._last_progress = (index, total, result)
            if result.success:
                self._pending_log.append(f"✓ {result.source.name} — {result.message}")
            elif result.skipped:
                self._pending_log.append(f"• {result.source.name} — {result.message}")
            else:
                self._pending_log.append(f"✕ {result.source.name} — {result.message}")

        elif kind == "scanned":
            self._handle_scanned(int(event[1]))

        elif kind == "done":
            _, results, cancelled, elapsed = event
            self._pending_done = (results, bool(cancelled), float(elapsed))

        elif kind == "fatal":
            self._pending_fatal = event[1]

        elif kind == "scan_done":
            _, generation, count, error = event
            self._handle_scan_done(int(generation), int(count), error)

    def _handle_scanned(self, total: int) -> None:
        self.total_files = total
        self._set_scan_badge(f"{to_fa(total)} فایل" if total else "فایلی نیست")
        if self.stat_total is not None:
            self.stat_total.configure(text=to_fa(total))
        if not total:
            if self.status_label is not None:
                self.status_label.configure(text="تصویری برای تبدیل پیدا نشد")
            return
        if self.status_label is not None:
            self.status_label.configure(text=f"در حال تبدیل {to_fa(total)} تصویر…")
        if self.progress_detail is not None:
            self.progress_detail.configure(text=f"۰ از {to_fa(total)} فایل")

    def _flush_progress(self) -> None:
        if self._last_progress is None:
            return

        index, total, result = self._last_progress

        if self.stat_success is not None:
            self.stat_success.configure(text=to_fa(self.success_count))
        if self.stat_skipped is not None:
            self.stat_skipped.configure(text=to_fa(self.skipped_count))
        if self.stat_error is not None:
            self.stat_error.configure(text=to_fa(self.error_count))
        if self.stat_total is not None:
            self.stat_total.configure(text=to_fa(total))

        progress = index / total if total else 0.0
        if self.progress_bar is not None:
            self.progress_bar.set(progress)
        if self.progress_percent is not None:
            self.progress_percent.configure(text=f"{to_fa(round(progress * 100))}٪")
        if self.progress_detail is not None:
            self.progress_detail.configure(text=f"{to_fa(index)} از {to_fa(total)} فایل")

        elapsed = max(0.01, time.perf_counter() - self._run_started_at)
        speed = index / elapsed
        if self.progress_meta is not None:
            if index < total and speed > 0:
                remaining = (total - index) / speed
                self.progress_meta.configure(
                    text=f"{to_fa(f'{speed:.1f}')} فایل/ثانیه • {format_duration(remaining)} باقی‌مانده"
                )
            else:
                self.progress_meta.configure(text=f"{to_fa(f'{speed:.1f}')} فایل/ثانیه")

        if self.status_label is not None:
            self.status_label.configure(text=f"در حال تبدیل: {shorten_text(result.source.name, 46)}")

    def _finish_run(self, results: list[ConversionResult], cancelled: bool, elapsed: float) -> None:
        self._run_active = False
        self._last_progress = None
        self.start_button.configure(state="normal")
        self.cancel_button.configure(state="disabled")
        self.cancel_button.grid_remove()

        processed = self.success_count + self.skipped_count + self.error_count
        duration_text = format_duration(elapsed)

        if cancelled:
            if self.status_label is not None:
                self.status_label.configure(
                    text=f"لغو شد • {to_fa(processed)} از {to_fa(self.total_files)} فایل"
                )
            self._append_log_lines([f"[CANCEL] Processed {processed} file(s) before cancellation."])
            if self.toast is not None:
                self.toast.show(
                    "عملیات لغو شد",
                    f"{to_fa(processed)} از {to_fa(self.total_files)} فایل پردازش شد.",
                    kind="info",
                    duration=4500,
                )
            self._schedule_scan()
            return

        if self.total_files == 0:
            if self.status_label is not None:
                self.status_label.configure(text="تصویری برای تبدیل پیدا نشد")
            self._append_log_lines(["[DONE] No supported image files were found."])
            if self.toast is not None:
                self.toast.show(
                    "تصویری پیدا نشد",
                    "در پوشه ورودی، فایل تصویری پشتیبانی‌شده وجود ندارد.",
                    kind="warning",
                    duration=6000,
                )
            self._schedule_scan()
            return

        if self.progress_bar is not None:
            self.progress_bar.set(1)
        if self.progress_percent is not None:
            self.progress_percent.configure(text="۱۰۰٪")
        if self.progress_detail is not None:
            self.progress_detail.configure(
                text=f"{to_fa(self.total_files)} از {to_fa(self.total_files)} فایل"
            )
        if self.progress_meta is not None:
            self.progress_meta.configure(text=f"زمان کل: {duration_text}")

        summary = (
            f"{to_fa(self.success_count)} موفق • "
            f"{to_fa(self.skipped_count)} ردشده • "
            f"{to_fa(self.error_count)} خطا"
        )
        if self.status_label is not None:
            self.status_label.configure(text=f"پایان • {summary}")

        self._append_log_lines(
            [
                f"[DONE] success={self.success_count} skipped={self.skipped_count} "
                f"errors={self.error_count} time={elapsed:.2f}s"
            ]
        )

        if self.toast is None:
            return

        open_action = ("باز کردن پوشه", self._open_output_folder)

        if self.error_count:
            self.toast.show(
                "تبدیل با خطا تمام شد",
                f"{summary} • گزارش را بررسی کنید.",
                kind="warning",
                action=open_action,
                duration=8000,
            )
        else:
            skipped_note = f" • {to_fa(self.skipped_count)} فایل رد شد" if self.skipped_count else ""
            self.toast.show(
                "تبدیل کامل شد",
                f"{to_fa(self.success_count)} تصویر در {duration_text} ذخیره شد{skipped_note}.",
                kind="success",
                action=open_action,
                duration=6500,
            )

        self._schedule_scan()

    def _handle_fatal(self, exc: Exception) -> None:
        self._run_active = False
        self._last_progress = None
        self.start_button.configure(state="normal")
        self.cancel_button.configure(state="disabled")
        self.cancel_button.grid_remove()
        if self.status_label is not None:
            self.status_label.configure(text="خطای غیرمنتظره")
        self._append_log_lines([f"[FATAL] {type(exc).__name__}: {exc}"])
        if self.toast is not None:
            self.toast.show(
                "خطای غیرمنتظره",
                str(exc) or type(exc).__name__,
                kind="error",
                duration=9000,
            )

    # ------------------------------------------------------------------
    # Log helpers
    # ------------------------------------------------------------------

    def _append_log(self, text: str) -> None:
        self._append_log_lines([text])

    def _append_log_lines(self, lines: list[str]) -> None:
        self._pending_log.extend(lines)
        self._flush_log()

    def _flush_log(self) -> None:
        if not self._pending_log:
            return
        lines = self._pending_log
        self._pending_log = []

        try:
            follow = self.log_box.yview()[1] >= 0.995
        except Exception:
            follow = True

        self.log_box.configure(state="normal")
        for line in lines:
            if line.startswith("✓"):
                tag = "ok"
            elif line.startswith("✕") or line.startswith("[FATAL]"):
                tag = "error"
            elif line.startswith("•"):
                tag = "skip"
            elif line.startswith("["):
                tag = "skip"
            else:
                tag = "meta"
            self.log_box.insert("end", line + "\n", tag)

        try:
            line_count = int(self.log_box.index("end-1c").split(".")[0])
            if line_count > 2200:
                self.log_box.delete("1.0", f"{line_count - 1600}.0")
        except Exception:
            pass

        if follow:
            self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def _clear_log(self) -> None:
        self.log_box.configure(state="normal")
        self.log_box.delete("1.0", "end")
        self.log_box.configure(state="disabled")

    # ------------------------------------------------------------------
    # Output folder
    # ------------------------------------------------------------------

    def _open_output_folder(self) -> None:
        text = self.output_var.get().strip()
        if not text:
            self._notify_error("پوشه خروجی انتخاب نشده", "ابتدا پوشه ذخیره خروجی را مشخص کنید.")
            return

        path = Path(text).expanduser()
        try:
            path.mkdir(parents=True, exist_ok=True)
            if sys.platform.startswith("win"):
                os.startfile(path)  # type: ignore[attr-defined]
            elif sys.platform == "darwin":
                subprocess.run(["open", str(path)], check=False)
            else:
                subprocess.run(["xdg-open", str(path)], check=False)
        except Exception as exc:
            self._notify_error("پوشه باز نشد", str(exc))

    # ------------------------------------------------------------------
    # Shutdown
    # ------------------------------------------------------------------

    def on_close(self) -> None:
        if self._run_active:
            if not messagebox.askyesno(
                "خروج از برنامه",
                "تبدیل هنوز در حال انجام است. پردازش متوقف و برنامه بسته شود؟",
            ):
                return
            self.cancel_event.set()

        self._closing = True
        self._save_settings_now()

        for job in (self._poll_job, self._settings_save_job, self._scan_after_job):
            if job is not None:
                try:
                    self.after_cancel(job)
                except Exception:
                    pass
        self._poll_job = None
        self._settings_save_job = None
        self._scan_after_job = None

        if self.toast is not None:
            self.toast.destroy()

        self.destroy()


def main() -> None:
    ctk.set_appearance_mode("dark")
    ctk.set_default_color_theme("blue")
    app = ProImageConverterApp()
    app.mainloop()


if __name__ == "__main__":
    main()
