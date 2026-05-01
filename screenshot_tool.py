"""
PinShot v1.3 - lightweight floating screenshot tool for Windows.

Provided by Jim | For Support, contact Jim
Project signature: PINSHOT-JIM-2026-PERSONAL-USE-7F3A9C2D
- Select a rectangular area on screen
- Up to 10 floating, always-on-top, resizable screenshot windows
- Markup: highlight, rect (filled or outline), ellipse, arrow, number,
  blur, text, spotlight; right-click for context menu
- Persistent preferences, single-instance, crash logging
- Free, no licensing, no watermark
"""

import asyncio
import ctypes
import ctypes.wintypes
import io
import json
import logging
import os
import sys
import threading
import time
import tkinter as tk
import traceback
from tkinter import colorchooser, filedialog, messagebox


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------
APP_NAME = "PinShot"
APP_VERSION = "1.3"
APP_AUTHOR = "Jim"
APP_COPYRIGHT = "Copyright (c) 2026 Jim. All rights reserved."
APP_PROVENANCE_ID = "PINSHOT-JIM-2026-PERSONAL-USE-7F3A9C2D"
APP_REPOSITORY = "https://github.com/jim72-commits/PinShot"
MAX_SCREENSHOTS = 10

HIGHLIGHT_COLORS = [
    ("#FFFF00", "Yellow"),
    ("#FF0000", "Red"),
    ("#00FF00", "Green"),
]
HIGHLIGHT_WIDTH = 14
HIGHLIGHT_ALPHA = 100  # 0-255

# Markup tools
TOOL_HIGHLIGHT = "highlight"
TOOL_RECT = "rect"
TOOL_RECT_FILLED = "rect_filled"
TOOL_ELLIPSE = "ellipse"
TOOL_ELLIPSE_FILLED = "ellipse_filled"
TOOL_ARROW = "arrow"
TOOL_NUMBER = "number"
TOOL_BLUR = "blur"
TOOL_TEXT = "text"
TOOL_SPOTLIGHT = "spotlight"
TOOLS = [
    (TOOL_HIGHLIGHT, "Highlight"),
    (TOOL_RECT, "Rectangle"),
    (TOOL_RECT_FILLED, "Rectangle (filled)"),
    (TOOL_ELLIPSE, "Ellipse"),
    (TOOL_ELLIPSE_FILLED, "Ellipse (filled)"),
    (TOOL_ARROW, "Arrow"),
    (TOOL_NUMBER, "Number"),
    (TOOL_TEXT, "Text"),
    (TOOL_SPOTLIGHT, "Spotlight"),
    (TOOL_BLUR, "Blur / Redact"),
]

BLUR_RADIUS = 14  # Gaussian blur radius for the blur tool

# Number-tool sizing. We target a roughly 1 cm physical diameter on screen
# so markers stay visually consistent across small and huge screenshots.
# Diameter ~1 cm => radius ~0.5 cm. At 96 DPI that's ~19 px; on hi-DPI
# monitors we read the actual DPI so the marker looks the same physical
# size as on a standard display. The user can dial up/down via the
# right-click "Number Size" submenu (presets multiply the base).
NUMBER_RADIUS_TARGET_CM = 0.5   # physical radius target (1 cm diameter)
NUMBER_RADIUS_MIN_PX = 12       # readability floor
NUMBER_RADIUS_MAX_PX = 56       # absolute ceiling even at XL on 4K hi-DPI
NUMBER_SIZE_PRESETS = [
    ("Small", 0.7),
    ("Medium", 1.0),
    ("Large", 1.4),
    ("Extra Large", 1.8),
]
NUMBER_SIZE_DEFAULT = 1.0

# Text-tool sizing. Same auto-scale + cap pattern as the number tool so a
# committed annotation does not balloon to 80+ px on a 4K screenshot.
TEXT_FONT_BASE_PX = 18
TEXT_FONT_MIN_PX = 12
TEXT_FONT_MAX_PX = 28

# Selection-overlay magnifier loupe.
# Odd LOUPE_SAMPLES so the cursor sits on a single, clearly identifiable
# center pixel. Larger sample count gives more context; lower zoom keeps text
# under the cursor still legible inside the loupe.
LOUPE_SAMPLES = 21       # source pixels shown across each side (odd)
LOUPE_ZOOM = 7           # display zoom factor (21*7 = 147 px zoom area)
LOUPE_LABEL_H = 22       # px reserved below zoom area for hex + coord labels
LOUPE_OFFSET = 28        # cursor-to-loupe offset

# Scandinavian / Swedish design palette
SCAN_BG = "#FAFAF7"            # warm off-white toolbar
SCAN_BORDER = "#D6D2C8"        # hairline warm gray border
SCAN_TEXT = "#2C3338"          # rich charcoal text
SCAN_MUTED = "#9A958C"         # warm muted gray
SCAN_ACCENT = "#5C7A92"        # muted Scandinavian blue
SCAN_ACCENT_HOVER = "#4A6680"  # deeper accent on hover
SCAN_ACCENT_FG = "#FFFFFF"     # white text on accent
SCAN_BTN_BG = "#FFFFFF"        # clean white (used for dropdown surface)
SCAN_BTN_HOVER = "#EFEDE5"     # subtle warm hover
SCAN_DANGER = "#C25450"        # muted brick red


# ---------------------------------------------------------------------------
# Lazy PIL loading - keeps app startup fast
# ---------------------------------------------------------------------------
Image = ImageTk = ImageGrab = None


def _ensure_pil():
    """Import PIL on first use; subsequent calls are no-ops."""
    global Image, ImageTk, ImageGrab
    if Image is None:
        from PIL import Image as _Image, ImageTk as _ImageTk, ImageGrab as _ImageGrab
        Image = _Image
        ImageTk = _ImageTk
        ImageGrab = _ImageGrab


# ---------------------------------------------------------------------------
# Filesystem helpers
# ---------------------------------------------------------------------------
def _base_dir():
    """Directory containing the running .exe (or the source file in dev)."""
    if getattr(sys, "frozen", False):
        return os.path.dirname(sys.executable)
    return os.path.dirname(os.path.abspath(__file__))


def _asset_path(filename):
    """Resolve a bundled asset path (handles PyInstaller --onefile)."""
    if getattr(sys, "frozen", False):
        base = getattr(sys, "_MEIPASS", os.path.dirname(sys.executable))
    else:
        base = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(base, filename)


def _appdata_dir():
    """Per-user state directory under %LOCALAPPDATA%\\PinShot. Created on demand.

    Used for logs, preferences and recent-state - things that should travel
    with the user, not with the .exe folder. Falls back to next to the .exe
    if LOCALAPPDATA is unavailable (e.g. running as SYSTEM).
    """
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("APPDATA") or _base_dir()
    path = os.path.join(base, APP_NAME)
    try:
        os.makedirs(path, exist_ok=True)
    except OSError:
        return _base_dir()
    return path


def _log_path():
    return os.path.join(_appdata_dir(), "pinshot.log")


def _prefs_path():
    return os.path.join(_appdata_dir(), "pinshot.json")


# ---------------------------------------------------------------------------
# Logging - rotating file in %LOCALAPPDATA%\PinShot\pinshot.log
# ---------------------------------------------------------------------------
log = logging.getLogger("pinshot")


def _setup_logging():
    """Configure file logging once at startup. Idempotent."""
    if log.handlers:
        return
    log.setLevel(logging.INFO)
    try:
        from logging.handlers import RotatingFileHandler
        h = RotatingFileHandler(
            _log_path(), maxBytes=512_000, backupCount=2, encoding="utf-8"
        )
    except Exception:
        # Fall back to a simple stream handler so we still get something.
        h = logging.StreamHandler()
    h.setFormatter(logging.Formatter(
        "%(asctime)s [%(levelname)s] %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))
    log.addHandler(h)
    log.info("--- PinShot %s starting (frozen=%s) ---",
             APP_VERSION, getattr(sys, "frozen", False))


def _install_excepthook():
    """Route uncaught exceptions to the log so we can debug field crashes."""
    def _hook(exc_type, exc_value, exc_tb):
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc_value, exc_tb)
            return
        msg = "".join(traceback.format_exception(exc_type, exc_value, exc_tb))
        log.error("Uncaught exception:\n%s", msg)
        try:
            messagebox.showerror(
                f"{APP_NAME} - unexpected error",
                f"PinShot hit an unexpected error and may not work correctly.\n\n"
                f"A diagnostic log has been written to:\n{_log_path()}\n\n"
                f"{exc_type.__name__}: {exc_value}",
            )
        except Exception:
            pass
    sys.excepthook = _hook
    # Tkinter swallows exceptions inside callbacks by default; redirect those too.
    def _tk_hook(_self, exc, val, tb):
        _hook(exc, val, tb)
    tk.Tk.report_callback_exception = _tk_hook


# ---------------------------------------------------------------------------
# Single-instance lock (Win32 named mutex). Holds the handle for process
# lifetime so the OS releases it automatically on exit/crash.
# ---------------------------------------------------------------------------
_SINGLE_INSTANCE_MUTEX = "Local\\PinShot_SingleInstance_Mutex_v1"
_single_instance_handle = None
ERROR_ALREADY_EXISTS = 183


def _acquire_single_instance():
    """Return True if we are the first PinShot instance, else broadcast a
    'show toolbar' message to the existing one and return False."""
    global _single_instance_handle
    try:
        kernel32 = ctypes.windll.kernel32
        kernel32.CreateMutexW.restype = ctypes.c_void_p
        kernel32.CreateMutexW.argtypes = [
            ctypes.c_void_p, ctypes.c_int, ctypes.c_wchar_p,
        ]
        handle = kernel32.CreateMutexW(None, 0, _SINGLE_INSTANCE_MUTEX)
        if not handle:
            return True  # CreateMutex failed - fail open rather than blocking
        if kernel32.GetLastError() == ERROR_ALREADY_EXISTS:
            kernel32.CloseHandle(handle)
            _broadcast_show_existing()
            return False
        _single_instance_handle = handle
        return True
    except Exception:
        log.exception("single-instance check failed; allowing launch")
        return True


def _show_trigger_path():
    """Trigger file polled by the first instance to know it should surface."""
    return os.path.join(_appdata_dir(), ".show_trigger")


def _broadcast_show_existing():
    """Tell the running PinShot instance to surface its toolbar.

    Writes a tiny trigger file in %LOCALAPPDATA%\\PinShot\\. The running
    instance polls for this once per second; if it appears the trigger is
    deleted and the toolbar surfaces. A trigger file is simpler and more
    robust than a Win32 message hook (which would need a custom WndProc).
    """
    try:
        with open(_show_trigger_path(), "w") as f:
            f.write("show")
    except Exception:
        log.exception("show-trigger write failed")


# ---------------------------------------------------------------------------
# Persistent preferences (%LOCALAPPDATA%\PinShot\pinshot.json)
# ---------------------------------------------------------------------------
class Prefs:
    """Lightweight JSON-backed key-value store. Loaded on first access, saved
    on every set so a crash never loses a preference. Single-writer model -
    only the main thread should call .set."""

    DEFAULTS = {
        "last_save_dir": "",
        "last_delay_seconds": 0,
        "default_tool": TOOL_HIGHLIGHT,
        "default_color": "#FF0000",
        "default_number_size": NUMBER_SIZE_DEFAULT,
        "toolbar_x": None,
        "toolbar_y": None,
        "first_run_complete": False,
    }

    def __init__(self):
        self._data = dict(self.DEFAULTS)
        self._loaded = False

    def _load(self):
        try:
            with open(_prefs_path(), "r", encoding="utf-8") as f:
                disk = json.load(f)
            if isinstance(disk, dict):
                # Only accept known keys; unknown keys are ignored on load
                # and dropped on next save - keeps the file from accumulating
                # cruft across versions.
                for k in self.DEFAULTS:
                    if k in disk:
                        self._data[k] = disk[k]
        except FileNotFoundError:
            pass
        except Exception:
            log.exception("prefs load failed; falling back to defaults")
        self._loaded = True

    def get(self, key, default=None):
        if not self._loaded:
            self._load()
        return self._data.get(key, default)

    def set(self, key, value):
        if not self._loaded:
            self._load()
        if self._data.get(key) == value:
            return
        self._data[key] = value
        self._save()

    def update(self, **kwargs):
        if not self._loaded:
            self._load()
        changed = False
        for k, v in kwargs.items():
            if self._data.get(k) != v:
                self._data[k] = v
                changed = True
        if changed:
            self._save()

    def _save(self):
        try:
            tmp = _prefs_path() + ".tmp"
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(self._data, f, indent=2)
            os.replace(tmp, _prefs_path())
        except Exception:
            log.exception("prefs save failed")


prefs = Prefs()


# ---------------------------------------------------------------------------
# Win32 helpers
# ---------------------------------------------------------------------------
def _virtual_screen_rect():
    """(x, y, w, h) covering all monitors via Win32 SM_*VIRTUALSCREEN metrics."""
    g = ctypes.windll.user32.GetSystemMetrics
    return g(76), g(77), g(78), g(79)


def _hex_to_rgb(hex_color):
    h = hex_color.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


# ---------------------------------------------------------------------------
# OkLab / OKLCH - perceptually uniform color space (Bjorn Ottosson, 2020).
#
# Why bother: HSL is mathematically broken - same "lightness" can look
# wildly different across hues. OkLab is engineered so that equal numerical
# changes produce equal *perceived* changes. Used for the color-picker
# readout and for the perceptual delta-E distance to white/black, which is
# the right metric for "is my highlight readable on this background?"
# ---------------------------------------------------------------------------
def _srgb_to_linear(c):
    """sRGB transfer function inverse. Input/output range 0-1."""
    return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4


def _rgb_to_oklab(r, g, b):
    """Convert 8-bit sRGB (0-255) to OkLab (L in [0,1], a/b ~ [-0.4, 0.4])."""
    r = _srgb_to_linear(r / 255.0)
    g = _srgb_to_linear(g / 255.0)
    b = _srgb_to_linear(b / 255.0)
    # sRGB -> LMS (Ottosson's matrix)
    l = 0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b
    m = 0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b
    s = 0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b
    # Cube root for the non-linear stage
    l_, m_, s_ = l ** (1 / 3), m ** (1 / 3), s ** (1 / 3)
    # LMS -> Lab
    L = 0.2104542553 * l_ + 0.7936177850 * m_ - 0.0040720468 * s_
    a = 1.9779984951 * l_ - 2.4285922050 * m_ + 0.4505937099 * s_
    b_ = 0.0259040371 * l_ + 0.7827717662 * m_ - 0.8086757660 * s_
    return L, a, b_


def _rgb_to_oklch(r, g, b):
    """OkLab in cylindrical form: (L, C chroma, h hue degrees 0-360)."""
    import math
    L, a, b_ = _rgb_to_oklab(r, g, b)
    C = math.hypot(a, b_)
    h = math.degrees(math.atan2(b_, a)) % 360
    return L, C, h


def _oklab_distance(rgb1, rgb2):
    """Perceptual Euclidean distance in OkLab space ('delta E_ok')."""
    L1, a1, b1 = _rgb_to_oklab(*rgb1)
    L2, a2, b2 = _rgb_to_oklab(*rgb2)
    return ((L1 - L2) ** 2 + (a1 - a2) ** 2 + (b1 - b2) ** 2) ** 0.5


# ---------------------------------------------------------------------------
# TrueType font resolution + cache
#
# Looking up a system font through Pillow's ImageFont.truetype hits the disk
# on every call. The same handful of (family, size) combinations are used
# every time we render strokes or text annotations - caching
# the resolved font drops several hundred microseconds off each composite.
# ---------------------------------------------------------------------------
_FONT_CACHE: dict = {}


def _font_with_fallback(families, size):
    """Return the first available TrueType font from `families` at `size`.

    Falls back to ImageFont.load_default() when none of the requested families
    can be loaded. Both the resolution result AND the load_default fallback
    are cached, so the only disk hit is on the very first miss for each
    (families, size) tuple.
    """
    families = tuple(families)
    key = (families, size)
    cached = _FONT_CACHE.get(key)
    if cached is not None:
        return cached
    from PIL import ImageFont
    font = None
    for family in families:
        try:
            font = ImageFont.truetype(family, size)
            break
        except OSError:
            continue
    if font is None:
        font = ImageFont.load_default()
    _FONT_CACHE[key] = font
    return font


# ---------------------------------------------------------------------------
# Stroke geometry helpers
# ---------------------------------------------------------------------------
def _rdp_simplify(points, epsilon):
    """Iterative Ramer-Douglas-Peucker line simplification.

    Returns a subset of `points` such that no removed point lies more than
    `epsilon` perpendicular distance from the kept polyline. Iterative form
    avoids Python's recursion-limit cliff for very long strokes.
    """
    n = len(points)
    if n <= 2:
        return list(points)
    keep = [False] * n
    keep[0] = keep[-1] = True
    stack = [(0, n - 1)]
    eps_sq = epsilon * epsilon
    while stack:
        i, j = stack.pop()
        if j - i < 2:
            continue
        x1, y1 = points[i]
        x2, y2 = points[j]
        dx = x2 - x1
        dy = y2 - y1
        len_sq = dx * dx + dy * dy
        max_d_sq = 0.0
        max_k = -1
        for k in range(i + 1, j):
            px, py = points[k]
            if len_sq == 0:
                ddx = px - x1
                ddy = py - y1
                d_sq = ddx * ddx + ddy * ddy
            else:
                # Perpendicular squared distance to segment
                num = (dy * px - dx * py + x2 * y1 - y2 * x1)
                d_sq = (num * num) / len_sq
            if d_sq > max_d_sq:
                max_d_sq = d_sq
                max_k = k
        if max_d_sq > eps_sq and max_k != -1:
            keep[max_k] = True
            stack.append((i, max_k))
            stack.append((max_k, j))
    return [points[i] for i in range(n) if keep[i]]


def _catmull_rom(points, segments=6):
    """Centripetal Catmull-Rom spline interpolation.

    Returns a smoothed list of points passing through every input control
    point. The curve uses a duplicated-endpoint padding so it starts and ends
    exactly at the input. `segments` is per-piece subdivision; 6 is a good
    balance of smoothness and point count.
    """
    n = len(points)
    if n < 3:
        return list(points)
    pad = [points[0]] + list(points) + [points[-1]]
    out = [points[0]]
    for i in range(len(pad) - 3):
        p0, p1, p2, p3 = pad[i], pad[i + 1], pad[i + 2], pad[i + 3]
        for j in range(1, segments + 1):
            t = j / segments
            t2 = t * t
            t3 = t2 * t
            x = 0.5 * (
                (2 * p1[0])
                + (-p0[0] + p2[0]) * t
                + (2 * p0[0] - 5 * p1[0] + 4 * p2[0] - p3[0]) * t2
                + (-p0[0] + 3 * p1[0] - 3 * p2[0] + p3[0]) * t3
            )
            y = 0.5 * (
                (2 * p1[1])
                + (-p0[1] + p2[1]) * t
                + (2 * p0[1] - 5 * p1[1] + 4 * p2[1] - p3[1]) * t2
                + (-p0[1] + 3 * p1[1] - 3 * p2[1] + p3[1]) * t3
            )
            out.append((x, y))
    return out


# ---------------------------------------------------------------------------
# Fast blur (3-pass box blur ~ Gaussian)
# ---------------------------------------------------------------------------
def _fast_blur(img, gauss_radius):
    """Single-pass box blur ~2x faster than GaussianBlur of the same radius.

    Pillow's BoxBlur is O(1) per pixel via running sums; GaussianBlur is
    O(r) per pixel via separable Gaussian. For redaction (where content
    just needs to be unreadable) the slight axis-aligned softness vs a
    true Gaussian is imperceptible at radius >= 8.

    Measured (1920x1080 RGBA): Gaussian(14) 38.8ms -> BoxBlur(14) 18.0ms.
    """
    from PIL import ImageFilter
    return img.filter(ImageFilter.BoxBlur(gauss_radius))


# ---------------------------------------------------------------------------
# Markup primitives (used by ScreenshotWindow when compositing)
# ---------------------------------------------------------------------------
def _draw_arrow_pil(draw, p1, p2, color, width):
    """Draw a solid line + filled triangular head from p1 to p2."""
    import math
    x1, y1 = p1
    x2, y2 = p2
    dx, dy = x2 - x1, y2 - y1
    length = math.hypot(dx, dy)
    if length < 1:
        return
    head_len = max(width * 4, 14)
    head_half = max(width * 2.5, 9)
    ux, uy = dx / length, dy / length
    # Body stops just before the arrowhead's back edge so they merge cleanly.
    body_end = (x2 - ux * head_len * 0.65, y2 - uy * head_len * 0.65)
    draw.line([p1, body_end], fill=color, width=width)
    back = (x2 - ux * head_len, y2 - uy * head_len)
    perp = (-uy, ux)
    left = (back[0] + perp[0] * head_half, back[1] + perp[1] * head_half)
    right = (back[0] - perp[0] * head_half, back[1] - perp[1] * head_half)
    draw.polygon([(x2, y2), left, right], fill=color)


def _draw_number_pil(draw, pos, radius, n, color, font):
    """Filled circle with a centered number label in white."""
    cx, cy = pos
    draw.ellipse(
        (cx - radius, cy - radius, cx + radius, cy + radius),
        fill=color, outline=(255, 255, 255, 255), width=max(2, radius // 8),
    )
    text = str(n)
    bbox = draw.textbbox((0, 0), text, font=font)
    tw = bbox[2] - bbox[0]
    th = bbox[3] - bbox[1]
    draw.text(
        (cx - tw / 2 - bbox[0], cy - th / 2 - bbox[1]),
        text, font=font, fill=(255, 255, 255, 255),
    )


# ---------------------------------------------------------------------------
# Clipboard - configure ctypes prototypes once at module load
# ---------------------------------------------------------------------------
_K32 = ctypes.windll.kernel32
_U32 = ctypes.windll.user32

_K32.GlobalAlloc.restype = ctypes.c_void_p
_K32.GlobalAlloc.argtypes = [ctypes.c_uint, ctypes.c_size_t]
_K32.GlobalLock.restype = ctypes.c_void_p
_K32.GlobalLock.argtypes = [ctypes.c_void_p]
_K32.GlobalUnlock.argtypes = [ctypes.c_void_p]
_K32.GlobalFree.restype = ctypes.c_void_p
_K32.GlobalFree.argtypes = [ctypes.c_void_p]
_U32.OpenClipboard.argtypes = [ctypes.c_void_p]
_U32.OpenClipboard.restype = ctypes.c_int
_U32.SetClipboardData.argtypes = [ctypes.c_uint, ctypes.c_void_p]
_U32.SetClipboardData.restype = ctypes.c_void_p
_U32.RegisterClipboardFormatW.argtypes = [ctypes.c_wchar_p]
_U32.RegisterClipboardFormatW.restype = ctypes.c_uint

_CF_DIB = 8
_GMEM_MOVEABLE = 0x0002

# Registered clipboard format for raw PNG bytes (resolved on first use, not
# at import time, so user32 init costs don't bleed into cold-start).
#
# Note on CF_HTML: an earlier revision also published CF_HTML containing
# <img src="file:///temp.png">. While that worked in Confluence/Jira/Notion,
# Outlook desktop preferentially picks CF_HTML over CF_DIB on paste and then
# refuses to load file:// images due to its "block external content" security
# policy - so the pasted image silently disappeared. We've reverted to
# CF_DIB + CF_PNG, which works everywhere users actually paste in practice
# (Outlook, Word, Teams, Slack desktop, Paint, browsers, modern web apps that
# use the Async Clipboard API to read image/png).
_CF_PNG = None


def _ensure_clipboard_formats():
    global _CF_PNG
    if _CF_PNG is None:
        _CF_PNG = _U32.RegisterClipboardFormatW("PNG")


def _alloc_clipboard_data(data: bytes):
    """Allocate movable global memory and copy `data` into it.

    Returns the global handle, or 0 on allocation failure. Caller transfers
    ownership to the OS via SetClipboardData; if SetClipboardData isn't
    called, the caller MUST free the handle with GlobalFree.
    """
    if not data:
        return 0
    hmem = _K32.GlobalAlloc(_GMEM_MOVEABLE, len(data))
    if not hmem:
        return 0
    ptr = _K32.GlobalLock(hmem)
    if not ptr:
        _K32.GlobalFree(hmem)
        return 0
    ctypes.memmove(ptr, data, len(data))
    _K32.GlobalUnlock(hmem)
    return hmem


def _open_clipboard_with_retry(retries: int = 5, delay_s: float = 0.05) -> bool:
    """OpenClipboard can transiently fail when another app has it locked.
    Retry a few times with a short backoff before giving up."""
    for i in range(retries):
        if _U32.OpenClipboard(None):
            return True
        if i < retries - 1:
            time.sleep(delay_s)
    return False


def _copy_image_to_clipboard(pil_img):
    """Place a PIL image on the Windows clipboard in two formats:

    - CF_DIB  -> Outlook, Word, Teams, Paint, Photoshop, all native Win32 apps
    - CF_PNG  -> modern apps & browsers reading via the Async Clipboard API
                 (preserves alpha, smaller payload)

    Each consumer picks whichever format it understands. If the clipboard is
    busy (rare), we retry briefly before raising.
    """
    _ensure_clipboard_formats()

    bmp_buf = io.BytesIO()
    pil_img.convert("RGB").save(bmp_buf, format="BMP")
    dib_data = bmp_buf.getvalue()[14:]  # strip 14-byte BMP file header

    png_buf = io.BytesIO()
    pil_img.convert("RGBA").save(png_buf, format="PNG", compress_level=3)
    png_data = png_buf.getvalue()

    handles = {
        _CF_DIB: _alloc_clipboard_data(dib_data),
        _CF_PNG: _alloc_clipboard_data(png_data),
    }
    if not all(handles.values()):
        for h in handles.values():
            if h:
                _K32.GlobalFree(h)
        raise OSError("Clipboard alloc failed")

    if not _open_clipboard_with_retry():
        for h in handles.values():
            _K32.GlobalFree(h)
        raise OSError("Could not open clipboard (locked by another process)")

    try:
        _U32.EmptyClipboard()
        for fmt, h in handles.items():
            # Once SetClipboardData succeeds, the OS owns the handle and we
            # must not free it. The call almost never fails after a successful
            # OpenClipboard - leaking a few KB on this rare path is acceptable.
            _U32.SetClipboardData(fmt, h)
    finally:
        _U32.CloseClipboard()


# ---------------------------------------------------------------------------
# Screenshot window
# ---------------------------------------------------------------------------
class ScreenshotWindow(tk.Toplevel):
    """A resizable, always-on-top window displaying a captured screenshot."""

    _RESIZE_DEBOUNCE_MS = 25

    def __init__(self, master, image, index: int, on_close):
        super().__init__(master)
        self.title(f"{APP_NAME} #{index}")
        self.attributes("-topmost", True)
        self.resizable(True, True)
        self.index = index
        self.on_close = on_close
        self.original_image = image  # PIL RGBA
        self._tool = prefs.get("default_tool") or TOOL_HIGHLIGHT
        self._draw_color = prefs.get("default_color") or HIGHLIGHT_COLORS[1][0]
        # Per-window scale for number/text markup. Restored from prefs so the
        # user's preference persists across captures.
        try:
            self._number_size_scale = float(
                prefs.get("default_number_size") or NUMBER_SIZE_DEFAULT
            )
        except (TypeError, ValueError):
            self._number_size_scale = NUMBER_SIZE_DEFAULT
        self._dpi_cache = None  # populated lazily by _window_dpi()
        self._drawing = False
        # Each stroke is a dict with a "type" key. See _composited_image for shape.
        self._strokes: list[dict] = []
        self._current_points = []
        self._draw_start = (0, 0)
        self._draw_end = (0, 0)
        self._last_rclick = (0, 0)  # screen coords of the last right-click
        self._photo = None
        self._composite_cache = None  # cached burned-in image
        self._resize_pending = None   # after-id for throttled redraw
        # Canvas dimensions snapshotted at drag start to avoid syscall-per-move
        self._draw_canvas_w = 1
        self._draw_canvas_h = 1

        self.protocol("WM_DELETE_WINDOW", self._close)

        # Size the canvas to the captured image's exact pixel dimensions so
        # the first paint is 1:1 with no resampling. Cap to ~90% of the screen
        # so very large captures still fit on screen with their aspect ratio
        # preserved. Tk auto-sizes the surrounding Toplevel around this canvas.
        iw, ih = image.size
        sw = max(1, self.winfo_screenwidth())
        sh = max(1, self.winfo_screenheight())
        max_w = int(sw * 0.90)
        max_h = int(sh * 0.85)
        cw, ch = iw, ih
        if cw > max_w or ch > max_h:
            scale = min(max_w / cw, max_h / ch)
            cw = max(120, int(cw * scale))
            ch = max(80, int(ch * scale))

        self.canvas = tk.Canvas(self, highlightthickness=0, width=cw, height=ch)
        self.canvas.pack(fill=tk.BOTH, expand=True)

        self.canvas.bind("<Configure>", self._on_resize)
        self.canvas.bind("<Button-3>", self._show_context_menu)
        self.canvas.bind("<ButtonPress-1>", self._on_draw_start)
        self.canvas.bind("<B1-Motion>", self._on_draw_move)
        self.canvas.bind("<ButtonRelease-1>", self._on_draw_end)
        self.bind("<Control-c>", lambda e: self._copy_to_clipboard())
        # Undo. Bound on the Toplevel (and the canvas, so it works whether the
        # window frame or the canvas has keyboard focus). Window-scoped only -
        # never a global hook, so it can't intercept keystrokes for other apps.
        self.bind("<Control-z>", lambda e: self._undo_stroke())
        self.bind("<Control-Z>", lambda e: self._undo_stroke())
        self.canvas.bind("<Control-z>", lambda e: self._undo_stroke())
        self.canvas.bind("<Control-Z>", lambda e: self._undo_stroke())

        self._build_context_menu()
        self._render()  # Configure event will trigger another render once mapped

    def _build_context_menu(self):
        menu_kwargs = dict(
            tearoff=0, bg=SCAN_BG, fg=SCAN_TEXT,
            activebackground=SCAN_ACCENT, activeforeground=SCAN_ACCENT_FG,
            bd=1, relief=tk.FLAT, font=("Segoe UI", 9),
        )
        self.ctx_menu = tk.Menu(self, **menu_kwargs)
        self.ctx_menu.add_command(label="Copy to Clipboard", command=self._copy_to_clipboard)
        self.ctx_menu.add_command(label="Grab Text", command=self._grab_text)
        self.ctx_menu.add_command(label="Save As...", command=self._save_as)
        self.ctx_menu.add_command(label="Pick Color at Cursor", command=self._pick_color_at_cursor)
        self.ctx_menu.add_command(label="Magnify Region", command=self._open_magnifier)
        self.ctx_menu.add_separator()

        # Tool submenu (radio-buttoned for current tool)
        self._tool_var = tk.StringVar(value=self._tool)
        tool_menu = tk.Menu(self.ctx_menu, **menu_kwargs)
        for tool_id, label in TOOLS:
            tool_menu.add_radiobutton(
                label=label, value=tool_id, variable=self._tool_var,
                command=lambda t=tool_id: self._set_tool(t),
            )
        self.ctx_menu.add_cascade(label="Tool", menu=tool_menu)

        # Color submenu - swatches only, no text. Three full-block chars
        # give a clearly-readable color sample without competing labels.
        color_menu = tk.Menu(self.ctx_menu, **menu_kwargs)
        for hex_c, _name in HIGHLIGHT_COLORS:
            color_menu.add_command(
                label="\u2588\u2588\u2588",
                foreground=hex_c,
                activeforeground=hex_c,
                command=lambda c=hex_c: self._set_color(c),
            )
        color_menu.add_separator()
        color_menu.add_command(label="Custom...", command=self._choose_color)
        self.ctx_menu.add_cascade(label="Color", menu=color_menu)

        # Number Size submenu - lets the user override the auto-scaled
        # marker radius. Applies to all numbers in this screenshot.
        self._num_size_var = tk.StringVar(value=str(self._number_size_scale))
        num_size_menu = tk.Menu(self.ctx_menu, **menu_kwargs)
        for label, scale in NUMBER_SIZE_PRESETS:
            num_size_menu.add_radiobutton(
                label=label, value=str(scale), variable=self._num_size_var,
                command=lambda v=scale: self._set_number_size(v),
            )
        self.ctx_menu.add_cascade(label="Number Size", menu=num_size_menu)

        self.ctx_menu.add_command(
            label="Undo", accelerator="Ctrl+Z", command=self._undo_stroke,
        )
        self.ctx_menu.add_command(label="Clear All", command=self._clear_strokes)
        self.ctx_menu.add_separator()
        self.ctx_menu.add_command(label="Close", command=self._close)

    # -------- stroke geometry --------
    def _stroke_bbox(self, s, iw, ih):
        """Pixel bbox covered by stroke s (with margin for line width)."""
        stype = s["type"]
        if stype == TOOL_HIGHLIGHT:
            pts = s.get("points", [])
            if len(pts) < 2:
                return None
            xs = [int(xf * iw) for xf, _ in pts]
            ys = [int(yf * ih) for _, yf in pts]
            margin = max(2, int(HIGHLIGHT_WIDTH * iw / 800)) // 2 + 2
            return (min(xs) - margin, min(ys) - margin,
                    max(xs) + margin + 1, max(ys) + margin + 1)
        if stype in (
            TOOL_RECT, TOOL_RECT_FILLED, TOOL_ELLIPSE, TOOL_ELLIPSE_FILLED,
            TOOL_ARROW, TOOL_BLUR, TOOL_SPOTLIGHT,
        ):
            x1 = int(s["p1"][0] * iw); y1 = int(s["p1"][1] * ih)
            x2 = int(s["p2"][0] * iw); y2 = int(s["p2"][1] * ih)
            l, t = min(x1, x2), min(y1, y2)
            r, b = max(x1, x2), max(y1, y2)
            if stype in (TOOL_RECT, TOOL_ELLIPSE):
                m = max(2, int(4 * iw / 800)) // 2 + 2
            elif stype in (TOOL_RECT_FILLED, TOOL_ELLIPSE_FILLED):
                m = 1
            elif stype == TOOL_ARROW:
                line_w = max(2, int(4 * iw / 800))
                m = max(line_w * 4, 14) + line_w + 2  # arrowhead extent
            elif stype == TOOL_SPOTLIGHT:
                # Spotlight darkens *outside* the rect, so its dirty rect is
                # the entire image, not just the rect itself.
                return (0, 0, iw, ih)
            else:  # TOOL_BLUR - exact user rectangle, no margin. The actual
                # blur is applied to exactly this rect in _build_region.
                m = 0
            return (l - m, t - m, r + m + 1, b + m + 1)
        if stype == TOOL_NUMBER:
            cx = int(s["pos"][0] * iw)
            cy = int(s["pos"][1] * ih)
            radius = self._number_radius(iw) + 2
            return (cx - radius, cy - radius, cx + radius + 1, cy + radius + 1)
        if stype == TOOL_TEXT:
            cx = int(s["pos"][0] * iw)
            cy = int(s["pos"][1] * ih)
            text = s.get("text", "")
            font_size = self._text_font_size(iw)
            # Conservative box: ~ font_size * len(text) * 0.7
            tw = int(font_size * 0.7 * max(1, len(text))) + 12
            th = font_size + 12
            return (cx - 4, cy - 4, cx + tw, cy + th)
        return None

    def _draw_stroke(self, draw, s, iw, ih, dx=0, dy=0,
                     stroke_w=None, line_w=None, number_radius=None, num_font=None):
        """Render one non-blur stroke onto an RGBA ImageDraw with offset (dx, dy)."""
        stype = s["type"]
        if stype == TOOL_BLUR:
            return
        color = s.get("color", "#FF0000")
        cr, cg, cb = _hex_to_rgb(color)
        if stype == TOOL_HIGHLIGHT:
            pts = [(int(xf * iw) + dx, int(yf * ih) + dy) for xf, yf in s["points"]]
            if len(pts) >= 2:
                draw.line(pts, fill=(cr, cg, cb, HIGHLIGHT_ALPHA),
                          width=stroke_w, joint="curve")
        elif stype == TOOL_RECT:
            x1 = int(s["p1"][0] * iw) + dx; y1 = int(s["p1"][1] * ih) + dy
            x2 = int(s["p2"][0] * iw) + dx; y2 = int(s["p2"][1] * ih) + dy
            draw.rectangle(
                (min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2)),
                outline=(cr, cg, cb, 255), width=line_w,
            )
        elif stype == TOOL_RECT_FILLED:
            x1 = int(s["p1"][0] * iw) + dx; y1 = int(s["p1"][1] * ih) + dy
            x2 = int(s["p2"][0] * iw) + dx; y2 = int(s["p2"][1] * ih) + dy
            # Semi-transparent fill so the underlying screenshot still shows
            draw.rectangle(
                (min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2)),
                fill=(cr, cg, cb, 110),
                outline=(cr, cg, cb, 255), width=line_w,
            )
        elif stype == TOOL_ELLIPSE:
            x1 = int(s["p1"][0] * iw) + dx; y1 = int(s["p1"][1] * ih) + dy
            x2 = int(s["p2"][0] * iw) + dx; y2 = int(s["p2"][1] * ih) + dy
            draw.ellipse(
                (min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2)),
                outline=(cr, cg, cb, 255), width=line_w,
            )
        elif stype == TOOL_ELLIPSE_FILLED:
            x1 = int(s["p1"][0] * iw) + dx; y1 = int(s["p1"][1] * ih) + dy
            x2 = int(s["p2"][0] * iw) + dx; y2 = int(s["p2"][1] * ih) + dy
            draw.ellipse(
                (min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2)),
                fill=(cr, cg, cb, 110),
                outline=(cr, cg, cb, 255), width=line_w,
            )
        elif stype == TOOL_ARROW:
            x1 = int(s["p1"][0] * iw) + dx; y1 = int(s["p1"][1] * ih) + dy
            x2 = int(s["p2"][0] * iw) + dx; y2 = int(s["p2"][1] * ih) + dy
            _draw_arrow_pil(draw, (x1, y1), (x2, y2), (cr, cg, cb, 255), line_w)
        elif stype == TOOL_NUMBER:
            cx = int(s["pos"][0] * iw) + dx
            cy = int(s["pos"][1] * ih) + dy
            _draw_number_pil(draw, (cx, cy), number_radius, s["n"],
                             (cr, cg, cb, 255), num_font)
        elif stype == TOOL_TEXT:
            cx = int(s["pos"][0] * iw) + dx
            cy = int(s["pos"][1] * ih) + dy
            font_size = self._text_font_size(iw)
            font = _font_with_fallback(("segoeuib.ttf", "segoeui.ttf"), font_size)
            text = s.get("text", "")
            # Soft backdrop pill for legibility against any image. Opacity
            # is intentionally moderate (was 180/255 - too dark, almost
            # opaque) so the underlying screenshot still reads through.
            bbox = draw.textbbox((cx, cy), text, font=font)
            pad = 4
            draw.rectangle(
                (bbox[0] - pad, bbox[1] - pad, bbox[2] + pad, bbox[3] + pad),
                fill=(0, 0, 0, 110),
            )
            draw.text((cx, cy), text, fill=(cr, cg, cb, 255), font=font)
        elif stype == TOOL_SPOTLIGHT:
            x1 = int(s["p1"][0] * iw) + dx; y1 = int(s["p1"][1] * ih) + dy
            x2 = int(s["p2"][0] * iw) + dx; y2 = int(s["p2"][1] * ih) + dy
            l_, t_ = min(x1, x2), min(y1, y2)
            r_, b_ = max(x1, x2), max(y1, y2)
            # Draw a 60% black overlay across the whole image bounded by dx,dy,
            # then carve out the rectangle by drawing a transparent rect on top.
            # ImageDraw can't punch holes in a fill, so we draw 4 darkening
            # rects around the spotlight. Bounds use the visible region.
            bg = (0, 0, 0, 150)
            # Top
            draw.rectangle((dx, dy, dx + iw, t_), fill=bg)
            # Bottom
            draw.rectangle((dx, b_, dx + iw, dy + ih), fill=bg)
            # Left
            draw.rectangle((dx, t_, l_, b_), fill=bg)
            # Right
            draw.rectangle((r_, t_, dx + iw, b_), fill=bg)
            # A subtle accent border around the spotlight rect
            draw.rectangle(
                (l_, t_, r_, b_),
                outline=(cr, cg, cb, 255), width=line_w,
            )

    def _window_dpi(self):
        """DPI of the monitor this window is on. Cached per-window. Falls
        back to 96 (standard Windows DPI) if the API isn't available."""
        if getattr(self, "_dpi_cache", None):
            return self._dpi_cache
        try:
            u32 = ctypes.windll.user32
            u32.GetDpiForWindow.restype = ctypes.c_uint
            u32.GetDpiForWindow.argtypes = [ctypes.c_void_p]
            hwnd = self.winfo_id()
            dpi = u32.GetDpiForWindow(hwnd) or 96
        except Exception:
            dpi = 96
        self._dpi_cache = dpi
        return dpi

    def _number_radius(self, iw):
        """Marker radius in image pixels, targeted at ~1 cm diameter on
        screen and adjusted for the user-selected size preset.

        Image pixels equal physical screen pixels at capture time (PinShot
        is per-monitor DPI aware), so converting cm -> px via the window's
        DPI gives the user a marker that's the same physical size whether
        the screenshot is 800 px wide or 4K. The image-width parameter is
        kept for API parity with _stroke_metrics callers but unused here.
        """
        del iw  # consistent radius regardless of capture size
        dpi = self._window_dpi()
        base = int(round(dpi * NUMBER_RADIUS_TARGET_CM / 2.54))
        scaled = int(round(base * self._number_size_scale))
        return max(NUMBER_RADIUS_MIN_PX, min(NUMBER_RADIUS_MAX_PX, scaled))

    def _text_font_size(self, iw):
        """Capped text font size (image pixels). Used both for the rendered
        annotation and for sizing the inline Entry preview."""
        auto = max(
            TEXT_FONT_MIN_PX,
            min(TEXT_FONT_MAX_PX, int(TEXT_FONT_BASE_PX * iw / 800)),
        )
        return auto

    def _stroke_metrics(self, iw, ih):
        """Per-image-size brush metrics + a cached bold font."""
        stroke_w = max(2, int(HIGHLIGHT_WIDTH * iw / 800))
        line_w = max(2, int(4 * iw / 800))
        number_radius = self._number_radius(iw)
        num_font = _font_with_fallback(
            ("segoeuib.ttf", "segoeui.ttf"), int(number_radius * 1.2),
        )
        return stroke_w, line_w, number_radius, num_font

    # -------- compositing --------
    def _composited_image(self):
        """Burn strokes into the image (cached)."""
        if self._composite_cache is not None:
            return self._composite_cache
        iw, ih = self.original_image.size
        # Build the full composite via a single bbox-covering region rebuild.
        self._composite_cache = self._build_region((0, 0, iw, ih))
        return self._composite_cache

    def _build_region(self, bbox):
        """Rebuild the composite for `bbox` only and return an RGBA image."""
        _ensure_pil()
        from PIL import ImageDraw
        iw, ih = self.original_image.size
        l, t, r, b = bbox
        rw, rh = r - l, b - t

        # 1. Crop the base image
        base = self.original_image.crop(bbox).convert("RGBA")

        # 2. Apply blur strokes that intersect this bbox
        for s in self._strokes:
            if s["type"] != TOOL_BLUR:
                continue
            sb = self._stroke_bbox(s, iw, ih)
            if sb is None:
                continue
            # Clip blur rect to image and intersect with bbox
            sl, st_, sr, sb_ = (
                max(0, sb[0]), max(0, sb[1]),
                min(iw, sb[2]), min(ih, sb[3]),
            )
            ll = max(sl, l); lt = max(st_, t)
            lr = min(sr, r); lb = min(sb_, b)
            if lr > ll and lb > lt:
                local = (ll - l, lt - t, lr - l, lb - t)
                region = base.crop(local)
                blurred = _fast_blur(region, BLUR_RADIUS)
                base.paste(blurred, (local[0], local[1]))

        # 3. Render non-blur strokes onto a bbox-sized overlay
        non_blur = [s for s in self._strokes if s["type"] != TOOL_BLUR]
        if non_blur:
            overlay = Image.new("RGBA", (rw, rh), (0, 0, 0, 0))
            draw = ImageDraw.Draw(overlay)
            stroke_w, line_w, number_radius, num_font = self._stroke_metrics(iw, ih)
            for s in non_blur:
                sb = self._stroke_bbox(s, iw, ih)
                if sb is None:
                    continue
                # Skip strokes whose bbox doesn't overlap the dirty rect
                if sb[2] <= l or sb[0] >= r or sb[3] <= t or sb[1] >= b:
                    continue
                self._draw_stroke(draw, s, iw, ih, dx=-l, dy=-t,
                                  stroke_w=stroke_w, line_w=line_w,
                                  number_radius=number_radius, num_font=num_font)
            base = Image.alpha_composite(base, overlay)

        return base

    def _add_stroke_incremental(self, stroke):
        """Dirty-rect update: paste only the new stroke's bbox onto the cache.

        ~10-100x faster than a full rebuild for typical strokes on a 1080p+
        screenshot. Falls back to full invalidate when:
        - the cache hasn't been built yet, or
        - the stroke is a blur (destructive on the base image).
        """
        if self._composite_cache is None:
            return
        if stroke["type"] == TOOL_BLUR:
            self._invalidate_composite()
            return
        iw, ih = self._composite_cache.size
        bbox = self._stroke_bbox(stroke, iw, ih)
        if bbox is None:
            return
        l, t, r, b = bbox
        l = max(0, l); t = max(0, t)
        r = min(iw, r); b = min(ih, b)
        if r <= l or b <= t:
            return
        region = self._build_region((l, t, r, b))
        self._composite_cache.paste(region, (l, t))

    def _invalidate_composite(self):
        self._composite_cache = None

    def _refresh(self):
        """Drop the cache and redraw."""
        self._invalidate_composite()
        self._render()

    def _render(self):
        """Resize the cached composite to the current canvas and draw it.

        Resamples with LANCZOS for crisp text/UI rendering. Resizes are
        already debounced (_RESIZE_DEBOUNCE_MS), so we render once after the
        user stops dragging - the extra ~30-70 ms of LANCZOS over BILINEAR is
        unnoticeable. When the canvas already matches the source image size
        we skip the resize entirely and draw at native 1:1.
        """
        w = self.canvas.winfo_width()
        h = self.canvas.winfo_height()
        if w <= 1 or h <= 1:
            return  # canvas not yet laid out; Configure will retrigger
        composite = self._composited_image()
        if composite.size != (w, h):
            resized = composite.resize((w, h), Image.LANCZOS)
        else:
            resized = composite
        self._photo = ImageTk.PhotoImage(resized)
        self.canvas.delete("all")
        self.canvas.create_image(0, 0, anchor=tk.NW, image=self._photo)

    def _on_resize(self, _event):
        """Coalesce rapid resize events into a single redraw."""
        if self._resize_pending is not None:
            self.after_cancel(self._resize_pending)
        self._resize_pending = self.after(self._RESIZE_DEBOUNCE_MS, self._resize_now)

    def _resize_now(self):
        self._resize_pending = None
        self._render()

    # -------- markup drawing --------
    def _on_draw_start(self, event):
        self._drawing = True
        self._draw_start = (event.x, event.y)
        self._draw_end = (event.x, event.y)
        # Snapshot the canvas dimensions once at drag start. Tk's winfo_*
        # calls are syscalls into the X/Tcl bridge - calling them on every
        # mouse-move event during a fast drag is a real cost. The canvas
        # cannot resize mid-drag, so a single snapshot is correct.
        self._draw_canvas_w = self.canvas.winfo_width() or 1
        self._draw_canvas_h = self.canvas.winfo_height() or 1
        if self._tool == TOOL_HIGHLIGHT:
            self._current_points = [
                (event.x / self._draw_canvas_w, event.y / self._draw_canvas_h)
            ]

    def _on_draw_move(self, event):
        if not self._drawing:
            return
        self._draw_end = (event.x, event.y)
        cw = self._draw_canvas_w
        ch = self._draw_canvas_h
        tool = self._tool
        if tool == TOOL_HIGHLIGHT:
            self._current_points.append((event.x / cw, event.y / ch))
            if len(self._current_points) >= 2:
                p1 = self._current_points[-2]
                p2 = self._current_points[-1]
                # Preview width must match the rendered width or the indicator
                # 'lies' to the user. Rendered width on-screen scales with the
                # canvas (HIGHLIGHT_WIDTH is calibrated at 800-px canvas width).
                preview_w = max(2, int(HIGHLIGHT_WIDTH * cw / 800))
                self.canvas.create_line(
                    p1[0] * cw, p1[1] * ch, p2[0] * cw, p2[1] * ch,
                    fill=self._draw_color, width=preview_w,
                    capstyle=tk.ROUND, joinstyle=tk.ROUND, tags="stroke",
                )
        elif tool in (TOOL_RECT, TOOL_RECT_FILLED, TOOL_SPOTLIGHT):
            self.canvas.delete("preview")
            sx, sy = self._draw_start
            opts = {"outline": self._draw_color, "width": 2, "tags": "preview"}
            if tool == TOOL_RECT_FILLED:
                opts["fill"] = self._draw_color
                opts["stipple"] = "gray50"
            elif tool == TOOL_SPOTLIGHT:
                opts["outline"] = SCAN_TEXT
                opts["dash"] = (4, 4)
            self.canvas.create_rectangle(sx, sy, event.x, event.y, **opts)
        elif tool in (TOOL_ELLIPSE, TOOL_ELLIPSE_FILLED):
            self.canvas.delete("preview")
            sx, sy = self._draw_start
            opts = {"outline": self._draw_color, "width": 2, "tags": "preview"}
            if tool == TOOL_ELLIPSE_FILLED:
                opts["fill"] = self._draw_color
                opts["stipple"] = "gray50"
            self.canvas.create_oval(sx, sy, event.x, event.y, **opts)
        elif tool == TOOL_ARROW:
            self.canvas.delete("preview")
            sx, sy = self._draw_start
            self.canvas.create_line(
                sx, sy, event.x, event.y,
                fill=self._draw_color, width=3,
                arrow=tk.LAST, arrowshape=(14, 16, 6),
                tags="preview",
            )
        elif tool == TOOL_BLUR:
            self.canvas.delete("preview")
            sx, sy = self._draw_start
            self.canvas.create_rectangle(
                sx, sy, event.x, event.y,
                outline=SCAN_TEXT, width=1,
                fill=SCAN_BG, stipple="gray50",
                tags="preview",
            )
        # TOOL_NUMBER: no preview during drag - it commits on release at the
        # press point regardless of motion.

    def _on_draw_end(self, _event):
        if not self._drawing:
            return
        self._drawing = False
        self.canvas.delete("preview")
        sx, sy = self._draw_start
        ex, ey = self._draw_end
        cw = self._draw_canvas_w
        ch = self._draw_canvas_h
        tool = self._tool
        stroke = None

        if tool == TOOL_HIGHLIGHT:
            if len(self._current_points) >= 2:
                # 1. RDP - drop redundant near-collinear points captured from
                #    high-frequency mouse motion (~7x reduction is typical).
                # 2. Catmull-Rom - resample the simplified control points into
                #    a smooth curve so the rendered line looks clean rather
                #    than a jagged polyline.
                # epsilon is in fractional canvas coords; ~2 pixels at 1000px.
                pts = _rdp_simplify(self._current_points, epsilon=0.0025)
                if len(pts) >= 3:
                    pts = _catmull_rom(pts, segments=6)
                stroke = {
                    "type": TOOL_HIGHLIGHT,
                    "color": self._draw_color,
                    "points": pts,
                }
        elif tool in (
            TOOL_RECT, TOOL_RECT_FILLED,
            TOOL_ELLIPSE, TOOL_ELLIPSE_FILLED,
            TOOL_SPOTLIGHT,
        ) and abs(ex - sx) >= 5 and abs(ey - sy) >= 5:
            stroke = {
                "type": tool,
                "color": self._draw_color,
                "p1": (sx / cw, sy / ch),
                "p2": (ex / cw, ey / ch),
            }
        elif tool == TOOL_TEXT:
            # Open an inline text editor at the click point. The actual stroke
            # is added when the user finishes typing.
            self._begin_text_edit(sx, sy)
            self._current_points = []
            return
        elif tool == TOOL_ARROW and (abs(ex - sx) >= 5 or abs(ey - sy) >= 5):
            stroke = {
                "type": TOOL_ARROW,
                "color": self._draw_color,
                "p1": (sx / cw, sy / ch),
                "p2": (ex / cw, ey / ch),
            }
        elif tool == TOOL_NUMBER:
            # Always 1-indexed contiguous; renumbering on undo keeps it that way.
            n = sum(1 for s in self._strokes if s["type"] == TOOL_NUMBER) + 1
            stroke = {
                "type": TOOL_NUMBER,
                "color": self._draw_color,
                "pos": (sx / cw, sy / ch),
                "n": n,
            }
        elif tool == TOOL_BLUR and abs(ex - sx) >= 5 and abs(ey - sy) >= 5:
            stroke = {
                "type": TOOL_BLUR,
                "p1": (sx / cw, sy / ch),
                "p2": (ex / cw, ey / ch),
            }

        if stroke:
            self._strokes.append(stroke)
            # Dirty-rect path: paste only the new stroke's bbox onto the cache.
            # _add_stroke_incremental falls back to invalidate-and-rebuild when
            # the cache is empty or the stroke is destructive (blur).
            self._add_stroke_incremental(stroke)
            self._render()
        self._current_points = []

    def _begin_text_edit(self, canvas_x, canvas_y):
        """Drop a small Tk Entry at (x,y) on the canvas; commit on Enter.

        The Entry's font size is computed to match what the committed text
        will look like ON THIS CANVAS after the image-level draw is
        downscaled - so the user's typing preview reads at the same size
        as the final result (no jarring resize on Enter).
        """
        cw = self.canvas.winfo_width() or 1
        ch = self.canvas.winfo_height() or 1
        iw, _ih = self.original_image.size

        # The committed text is rendered at `_text_font_size(iw)` image px.
        # When the composite is resized to the canvas it appears at
        # `font_px * cw / iw`, so we mirror that for the Entry. Tk's
        # negative font size means "pixels", which is exactly what we want.
        font_px_image = self._text_font_size(iw)
        entry_px = max(8, int(round(font_px_image * cw / max(iw, 1))))

        entry = tk.Entry(
            self.canvas, font=("Segoe UI Semibold", -entry_px),
            fg=self._draw_color, bg="#000000", insertbackground="#FFFFFF",
            relief=tk.FLAT, bd=0,
        )
        # Width in characters - scaled with font so the Entry box stays
        # visually similar across image sizes without overflowing.
        entry_chars = max(20, min(40, 240 // max(8, entry_px // 2)))
        entry_id = self.canvas.create_window(
            canvas_x, canvas_y, anchor="nw", window=entry,
            width=int(entry_chars * entry_px * 0.7), tags="text_edit",
        )
        entry.focus_set()

        def commit(_e=None):
            text = entry.get().strip()
            self.canvas.delete(entry_id)
            try:
                entry.destroy()
            except Exception:
                pass
            if not text:
                return
            stroke = {
                "type": TOOL_TEXT,
                "color": self._draw_color,
                "pos": (canvas_x / cw, canvas_y / ch),
                "text": text,
            }
            self._strokes.append(stroke)
            self._add_stroke_incremental(stroke)
            self._render()

        def cancel(_e=None):
            self.canvas.delete(entry_id)
            try:
                entry.destroy()
            except Exception:
                pass

        entry.bind("<Return>", commit)
        entry.bind("<Escape>", cancel)
        entry.bind("<FocusOut>", commit)

    def _set_tool(self, tool):
        self._tool = tool
        prefs.set("default_tool", tool)

    def _set_color(self, color):
        self._draw_color = color
        prefs.set("default_color", color)

    def _set_number_size(self, scale):
        """Update the marker scale for THIS window and re-render existing
        numbers. Persisted so future captures reuse the choice."""
        self._number_size_scale = float(scale)
        prefs.set("default_number_size", float(scale))
        if any(s["type"] == TOOL_NUMBER for s in self._strokes):
            self._refresh()

    def _choose_color(self):
        result = colorchooser.askcolor(
            initialcolor=self._draw_color, parent=self, title="Markup Color"
        )
        if result and result[1]:
            self._draw_color = result[1]

    def _open_magnifier(self):
        """Open a small floating loupe that follows the cursor over THIS
        screenshot only - same UX as the selection-time magnifier but for
        post-capture inspection. Closed by clicking it or pressing ESC.
        """
        cw = self.canvas.winfo_width() or 1
        ch = self.canvas.winfo_height() or 1
        if cw <= 1 or ch <= 1:
            return

        loupe = tk.Toplevel(self)
        loupe.overrideredirect(True)
        loupe.attributes("-topmost", True)
        loupe.configure(bg="#2C3338")
        size = 180  # 6x6 source pixels at zoom 30 - clear pixel preview
        cv = tk.Canvas(
            loupe, width=size, height=size, highlightthickness=0,
            bg="#2C3338",
        )
        cv.pack(padx=2, pady=2)

        composite = self._composited_image()
        iw, ih = composite.size
        samples = 6
        zoom = size // samples
        half = samples // 2
        # Track last sampled image pixel - skip the crop + resize + redraw
        # entirely when the cursor hasn't moved into a new pixel cell.
        state = {"ix": None, "iy": None}

        def update():
            if not loupe.winfo_exists():
                return
            mx = self.winfo_pointerx() - self.canvas.winfo_rootx()
            my = self.winfo_pointery() - self.canvas.winfo_rooty()
            if 0 <= mx < cw and 0 <= my < ch:
                ix = int(mx * iw / cw)
                iy = int(my * ih / ch)
                if (ix, iy) != (state["ix"], state["iy"]):
                    state["ix"], state["iy"] = ix, iy
                    region = composite.crop(
                        (ix - half, iy - half, ix + half + 1, iy + half + 1)
                    )
                    zoomed = region.resize(
                        (zoom * samples, zoom * samples), Image.NEAREST
                    )
                    cv._photo = ImageTk.PhotoImage(zoomed)
                    cv.delete("all")
                    cv.create_image(0, 0, anchor=tk.NW, image=cv._photo)
                    c = size // 2
                    cv.create_rectangle(c - zoom // 2, c - zoom // 2,
                                        c + zoom // 2, c + zoom // 2,
                                        outline="#FFB300", width=2)
                # Reposition every tick so the loupe follows the cursor even
                # when the underlying pixel hasn't changed.
                lx = self.winfo_pointerx() + 24
                ly = self.winfo_pointery() + 24
                loupe.geometry(f"+{lx}+{ly}")
            loupe.after(40, update)

        def close(_e=None):
            try:
                loupe.destroy()
            except Exception:
                pass

        cv.bind("<ButtonPress>", close)
        loupe.bind("<Escape>", close)
        loupe.focus_force()
        update()

    def _pick_color_at_cursor(self):
        """Sample the pixel at the last right-click position, copy hex."""
        rx, ry = self._last_rclick
        canvas_x = rx - self.canvas.winfo_rootx()
        canvas_y = ry - self.canvas.winfo_rooty()
        cw = self.canvas.winfo_width() or 1
        ch = self.canvas.winfo_height() or 1
        iw, ih = self.original_image.size
        ix = max(0, min(iw - 1, int(canvas_x * iw / cw)))
        iy = max(0, min(ih - 1, int(canvas_y * ih / ch)))
        pixel = self.original_image.getpixel((ix, iy))
        if isinstance(pixel, int):  # paletted images
            pixel = (pixel, pixel, pixel)
        r, g, b = pixel[:3]
        hex_color = f"#{r:02X}{g:02X}{b:02X}"
        try:
            self.clipboard_clear()
            self.clipboard_append(hex_color)
        except tk.TclError:
            pass
        self._show_color_toast(rx, ry, hex_color, (r, g, b))

    def _show_color_toast(self, x_root, y_root, hex_color, rgb):
        """Transient popup confirming the picked color, with OKLCH readout
        and contrast hints (perceptual delta-E to white and black)."""
        # Compute OkLab once and reuse - _rgb_to_oklch and the two distance
        # calls would otherwise re-run the sRGB->OkLab conversion three times.
        import math
        L, a, b_ = _rgb_to_oklab(*rgb)
        C = math.hypot(a, b_)
        h = math.degrees(math.atan2(b_, a)) % 360
        L_w, a_w, b_w = _rgb_to_oklab(255, 255, 255)
        L_k, a_k, b_k = _rgb_to_oklab(0, 0, 0)
        d_white = ((L - L_w) ** 2 + (a - a_w) ** 2 + (b_ - b_w) ** 2) ** 0.5
        d_black = ((L - L_k) ** 2 + (a - a_k) ** 2 + (b_ - b_k) ** 2) ** 0.5
        # Show the perceptually-closer of black/white as the "contrast partner".
        contrast_label = "near black" if d_black < d_white else "near white"
        d_main = min(d_black, d_white)

        toast = tk.Toplevel(self)
        toast.overrideredirect(True)
        toast.attributes("-topmost", True)
        toast.configure(bg=SCAN_BORDER)
        inner = tk.Frame(toast, bg=SCAN_BG, padx=10, pady=7)
        inner.pack(padx=1, pady=1)
        tk.Frame(
            inner, bg=hex_color, width=22, height=22,
            highlightthickness=1, highlightbackground=SCAN_BORDER,
        ).pack(side=tk.LEFT, padx=(0, 10))
        text = tk.Frame(inner, bg=SCAN_BG)
        text.pack(side=tk.LEFT, anchor="w")
        tk.Label(
            text, text=f"{hex_color}  copied",
            font=("Segoe UI Semibold", 9), fg=SCAN_TEXT, bg=SCAN_BG,
        ).pack(anchor="w")
        tk.Label(
            text,
            text=f"oklch({L:.2f} {C:.3f} {h:.0f})  -  \u0394E={d_main:.2f} ({contrast_label})",
            font=("Segoe UI", 8), fg=SCAN_MUTED, bg=SCAN_BG,
        ).pack(anchor="w")
        toast.update_idletasks()
        toast.geometry(f"+{x_root + 12}+{y_root + 12}")
        toast.after(2200, toast.destroy)

    def _renumber_steps(self):
        """Re-sequence number markers from 1..N in insertion order."""
        n = 0
        for s in self._strokes:
            if s.get("type") == TOOL_NUMBER:
                n += 1
                s["n"] = n

    def _undo_stroke(self):
        if not self._strokes:
            return
        last = self._strokes.pop()
        # If we removed a numbered marker, renumber the rest so the sequence
        # stays 1..N - same UX as Snagit / ShareX. Forces a full rebuild
        # because every remaining number marker may have shifted.
        was_number = last.get("type") == TOOL_NUMBER
        if was_number:
            self._renumber_steps()
            self._refresh()
            return
        # Incremental undo: rebuild only the bbox of the removed stroke.
        # Blur is destructive on the base, so its undo always needs a full rebuild.
        if self._composite_cache is None or last["type"] == TOOL_BLUR:
            self._invalidate_composite()
            self._render()
            return
        iw, ih = self._composite_cache.size
        bbox = self._stroke_bbox(last, iw, ih)
        if bbox is None:
            self._render()
            return
        l, t, r, b = bbox
        l = max(0, l); t = max(0, t); r = min(iw, r); b = min(ih, b)
        if r > l and b > t:
            region = self._build_region((l, t, r, b))
            self._composite_cache.paste(region, (l, t))
        self._render()

    def _clear_strokes(self):
        if self._strokes:
            self._strokes.clear()
            self._refresh()

    # -------- context-menu actions --------
    def _show_context_menu(self, event):
        self._last_rclick = (event.x_root, event.y_root)
        self.ctx_menu.tk_popup(event.x_root, event.y_root)

    def _copy_to_clipboard(self):
        try:
            _copy_image_to_clipboard(self._composited_image())
        except Exception as e:
            messagebox.showerror("Clipboard Error", str(e), parent=self)

    def _grab_text(self):
        """Run native Windows OCR in a worker thread; result -> clipboard."""
        img = self._composited_image().convert("RGB")
        self.config(cursor="wait")
        threading.Thread(target=self._ocr_worker, args=(img,), daemon=True).start()

    def _ocr_worker(self, img):
        try:
            import winrt.windows.media.ocr as w_ocr
            import winrt.windows.graphics.imaging as w_imaging
            import winrt.windows.storage.streams as w_streams

            async def _run():
                up = img.resize((img.width * 2, img.height * 2), Image.LANCZOS)
                buf = io.BytesIO()
                # Throwaway buffer for OCR; optimize for encode speed not size.
                up.convert("RGBA").save(buf, format="PNG", compress_level=1)

                stream = w_streams.InMemoryRandomAccessStream()
                writer = w_streams.DataWriter(stream)
                writer.write_bytes(buf.getvalue())
                await writer.store_async()
                writer.detach_stream()
                stream.seek(0)

                decoder = await w_imaging.BitmapDecoder.create_async(stream)
                bitmap = await decoder.get_software_bitmap_async()
                engine = w_ocr.OcrEngine.try_create_from_user_profile_languages()
                if engine is None:
                    return ""
                result = await engine.recognize_async(bitmap)
                return result.text

            text = asyncio.run(_run())
            self.after(0, lambda: self._on_ocr_done(text))
        except Exception as e:
            err = str(e)
            self.after(0, lambda: self._on_ocr_error(err))

    def _on_ocr_done(self, text):
        self.config(cursor="")
        if not text or not text.strip():
            messagebox.showinfo(
                "Grab Text", "No text detected in this screenshot.", parent=self
            )
            return
        self.clipboard_clear()
        self.clipboard_append(text.strip())

    def _on_ocr_error(self, error):
        self.config(cursor="")
        messagebox.showerror("Grab Text", f"OCR failed:\n{error}", parent=self)

    def _save_as(self):
        initial_dir = prefs.get("last_save_dir") or ""
        path = filedialog.asksaveasfilename(
            parent=self, title="Save Screenshot",
            defaultextension=".png",
            initialdir=initial_dir if initial_dir and os.path.isdir(initial_dir) else None,
            filetypes=[
                ("PNG Image (lossless)", "*.png"),
                ("WebP Image (lossless, smaller)", "*.webp"),
                ("JPEG Image", "*.jpg *.jpeg"),
                ("BMP Image", "*.bmp"),
                ("All Files", "*.*"),
            ],
        )
        if not path:
            return
        prefs.set("last_save_dir", os.path.dirname(path))
        img = self._composited_image().copy()
        self.config(cursor="wait")
        threading.Thread(
            target=self._save_worker, args=(path, img), daemon=True
        ).start()

    def _save_worker(self, path, img):
        try:
            ext = os.path.splitext(path)[1].lower()
            if ext == ".png":
                img.save(path, "PNG")
            elif ext == ".webp":
                img.save(path, "WebP", lossless=True, method=4, quality=100, exact=True)
            elif ext in (".jpg", ".jpeg"):
                img.convert("RGB").save(path, "JPEG", quality=92, optimize=True)
            elif ext == ".bmp":
                img.convert("RGB").save(path, "BMP")
            else:
                img.save(path)
            self.after(0, self._on_save_done)
        except Exception as e:
            err = str(e)
            self.after(0, lambda: self._on_save_error(err))

    def _on_save_done(self):
        try:
            self.config(cursor="")
        except tk.TclError:
            pass

    def _on_save_error(self, err):
        try:
            self.config(cursor="")
        except tk.TclError:
            pass
        messagebox.showerror("Save Error", err, parent=self)

    def _close(self):
        self.on_close(self)
        self.destroy()


# ---------------------------------------------------------------------------
# Selection overlay
# ---------------------------------------------------------------------------
# ---------------------------------------------------------------------------
# Tooltip - small Scandinavian-styled hover hint for toolbar buttons
# ---------------------------------------------------------------------------
class Tooltip:
    """Lightweight, lazy hover tooltip. ~40 LOC, no external deps.

    Tooltips appear after a 600ms hover delay so they never feel intrusive,
    and disappear instantly on Leave / ButtonPress. Only one Tk.Toplevel is
    materialised at a time per Tooltip instance, and only on first show.
    """

    DELAY_MS = 600

    def __init__(self, widget, text):
        self.widget = widget
        self.text = text
        self._tip = None
        self._after_id = None
        widget.bind("<Enter>", self._on_enter, add="+")
        widget.bind("<Leave>", self._on_leave, add="+")
        widget.bind("<ButtonPress>", self._on_leave, add="+")

    def _on_enter(self, _e):
        self._cancel()
        self._after_id = self.widget.after(self.DELAY_MS, self._show)

    def _on_leave(self, _e):
        self._cancel()
        self._hide()

    def _cancel(self):
        if self._after_id:
            try:
                self.widget.after_cancel(self._after_id)
            except Exception:
                pass
            self._after_id = None

    def _show(self):
        if self._tip or not self.widget.winfo_exists():
            return
        x = self.widget.winfo_rootx() + self.widget.winfo_width() // 2
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 6
        tip = tk.Toplevel(self.widget)
        tip.overrideredirect(True)
        tip.attributes("-topmost", True)
        tip.configure(bg=SCAN_TEXT)  # solid charcoal background
        tk.Label(
            tip, text=self.text,
            font=("Segoe UI", 8), fg="#FFFFFF", bg=SCAN_TEXT,
            padx=8, pady=4,
        ).pack()
        tip.update_idletasks()
        # Center horizontally under the widget
        tip.geometry(f"+{x - tip.winfo_reqwidth() // 2}+{y}")
        self._tip = tip

    def _hide(self):
        if self._tip:
            try:
                self._tip.destroy()
            except Exception:
                pass
            self._tip = None


class SelectionOverlay(tk.Toplevel):
    """Full-virtual-screen overlay for selecting a rectangular region."""

    SNAP_THRESHOLD_PX = 8  # snap distance for window-edge alignment

    def __init__(self, master, on_capture, on_cancel=None, freeze_image=None):
        super().__init__(master)
        # Hide while we set up so the source-image grab below doesn't include
        # this overlay in the captured pixels used by the magnifier.
        self.withdraw()

        self.on_capture = on_capture
        self._on_cancel = on_cancel
        self._start_x = self._start_y = 0
        self._rect_id = None
        self._loupe_photo = None

        vx, vy, vw, vh = _virtual_screen_rect()
        self._vx = vx
        self._vy = vy
        self._vw = vw
        self._vh = vh

        # Enumerate visible top-level window rects once (snap targets).
        # Done at construction so we don't pay enumeration cost per mouse move.
        self._snap_targets = self._enum_window_rects()

        # Source image used by the magnifier loupe. The overlay canvas spans the
        # virtual screen, so source pixels share canvas coordinates.
        _ensure_pil()
        if freeze_image is not None:
            self._source_image = freeze_image
        else:
            try:
                self._source_image = ImageGrab.grab(all_screens=True)
            except Exception:
                self._source_image = None

        self.overrideredirect(True)
        self.attributes("-topmost", True)
        if freeze_image is None:
            self.attributes("-alpha", 0.3)
        self.configure(bg="black")
        self.config(cursor="crosshair")
        self.geometry(f"{vw}x{vh}+{vx}+{vy}")

        self.canvas = tk.Canvas(self, bg="black", highlightthickness=0)
        self.canvas.pack(fill=tk.BOTH, expand=True)

        if freeze_image is not None:
            self._photo = ImageTk.PhotoImage(freeze_image)
            self.canvas.create_image(0, 0, anchor=tk.NW, image=self._photo)

        self.canvas.bind("<Motion>", self._on_motion)
        self.canvas.bind("<ButtonPress-1>", self._on_press)
        self.canvas.bind("<B1-Motion>", self._on_drag)
        self.canvas.bind("<ButtonRelease-1>", self._on_release)
        self.canvas.bind("<Leave>", lambda e: self.canvas.delete("loupe"))
        self.bind("<Escape>", lambda e: self._cancel())
        self.deiconify()
        self.focus_force()

    # -------- magnifier loupe --------
    def _update_loupe(self, cx, cy):
        """Render a clean magnifier loupe with pixel grid, crosshair, and label.

        Layout:
            +---------------------+
            |  zoomed pixel area  |  <- LOUPE_SAMPLES * LOUPE_ZOOM square
            |   (with grid +      |
            |    crosshair +      |
            |    center marker)   |
            +---------------------+
            | #RRGGBB    1234,567 |  <- label strip (LOUPE_LABEL_H tall)
            +---------------------+
        """
        if self._source_image is None:
            return
        sw, sh = self._source_image.size
        if not (0 <= cx < sw and 0 <= cy < sh):
            self.canvas.delete("loupe")
            return

        from PIL import ImageDraw
        zoom = LOUPE_ZOOM
        samples = LOUPE_SAMPLES
        half = samples // 2
        zoom_size = samples * zoom

        # Crop the source region. Out-of-bounds (cursor near screen edge)
        # auto-pads with the source image's fill which is fine for context.
        region = self._source_image.crop(
            (cx - half, cy - half, cx + half + 1, cy + half + 1)
        )
        zoomed = region.resize((zoom_size, zoom_size), Image.NEAREST)

        # Compose into a slightly-larger canvas with a label strip below.
        total_w = zoom_size
        total_h = zoom_size + LOUPE_LABEL_H
        canvas_img = Image.new("RGBA", (total_w, total_h), (250, 250, 247, 255))
        canvas_img.paste(zoomed, (0, 0))
        draw = ImageDraw.Draw(canvas_img)

        # 1. Faint pixel grid every `zoom` pixels (intra-pixel boundaries).
        if zoom >= 5:
            grid = (0, 0, 0, 55)
            for i in range(1, samples):
                v = i * zoom
                draw.line([(v, 0), (v, zoom_size - 1)], fill=grid, width=1)
                draw.line([(0, v), (zoom_size - 1, v)], fill=grid, width=1)

        # 2. Two-tone crosshair through the zoom area with a gap that exposes
        #    the center pixel. Black outline first, then white inner stroke.
        mid = zoom_size // 2
        gap = zoom // 2 + 2  # half a center pixel + a little breathing room
        for color, w in (((0, 0, 0, 220), 3), ((255, 255, 255, 235), 1)):
            # Vertical
            draw.line([(mid, 0), (mid, mid - gap)], fill=color, width=w)
            draw.line([(mid, mid + gap), (mid, zoom_size - 1)], fill=color, width=w)
            # Horizontal
            draw.line([(0, mid), (mid - gap, mid)], fill=color, width=w)
            draw.line([(mid + gap, mid), (zoom_size - 1, mid)], fill=color, width=w)

        # 3. Center pixel highlight - amber on dark backing for high contrast.
        cl = mid - zoom // 2
        cr = mid + zoom // 2 + (1 if zoom % 2 else 0)
        draw.rectangle((cl - 2, cl - 2, cr + 1, cr + 1),
                       outline=(0, 0, 0, 240), width=1)
        draw.rectangle((cl - 1, cl - 1, cr, cr),
                       outline=(255, 198, 64, 255), width=2)

        # 4. Label strip: hex code (left) + screen coords (right).
        label_y = zoom_size
        # Background separator
        draw.line([(0, label_y), (total_w, label_y)], fill=(214, 210, 200, 255), width=1)
        # Sample center pixel from the source for accurate hex.
        center_px = self._source_image.getpixel((cx, cy))
        if isinstance(center_px, int):
            center_px = (center_px, center_px, center_px)
        r, g, b = center_px[:3]
        hex_text = f"#{r:02X}{g:02X}{b:02X}"
        coord_text = f"{cx + self._vx}, {cy + self._vy}"
        font = _font_with_fallback(("segoeuib.ttf", "segoeui.ttf"), 11)
        text_y = label_y + (LOUPE_LABEL_H - 11) // 2 - 1
        # Color swatch - small filled square left of the hex code
        sw_x = 6
        sw_y = label_y + (LOUPE_LABEL_H - 11) // 2
        draw.rectangle((sw_x, sw_y, sw_x + 11, sw_y + 11),
                       fill=(r, g, b, 255), outline=(214, 210, 200, 255), width=1)
        draw.text((sw_x + 16, text_y), hex_text,
                  font=font, fill=(44, 51, 56, 255))
        coord_w = draw.textlength(coord_text, font=font)
        draw.text((total_w - coord_w - 6, text_y), coord_text,
                  font=font, fill=(106, 100, 92, 255))

        self._loupe_photo = ImageTk.PhotoImage(canvas_img)

        # 5. Position the loupe near the cursor, flip if it would clip an edge.
        x = cx + LOUPE_OFFSET
        y = cy + LOUPE_OFFSET
        if x + total_w + 4 > self._vw:
            x = cx - LOUPE_OFFSET - total_w
        if y + total_h + 4 > self._vh:
            y = cy - LOUPE_OFFSET - total_h
        x = max(2, x)
        y = max(2, y)

        self.canvas.delete("loupe")
        # Drop-shadow + frame for visibility on light & dark backgrounds.
        self.canvas.create_rectangle(
            x + 2, y + 2, x + total_w + 3, y + total_h + 3,
            outline="", fill="#000000", stipple="gray25", tags="loupe",
        )
        self.canvas.create_rectangle(
            x - 1, y - 1, x + total_w, y + total_h,
            outline="#2C3338", width=1, tags="loupe",
        )
        self.canvas.create_image(
            x, y, anchor=tk.NW, image=self._loupe_photo, tags="loupe",
        )

    def _update_size_readout(self, x1, y1, x2, y2):
        w = abs(x2 - x1)
        h = abs(y2 - y1)
        text = f"{w} \u00d7 {h}"
        # Position just below the bottom-right corner of the selection.
        tx = max(x1, x2) + 8
        ty = max(y1, y2) + 8
        self.canvas.delete("size_readout")
        text_id = self.canvas.create_text(
            tx, ty, anchor=tk.NW, text=text,
            font=("Segoe UI", 9), fill=SCAN_TEXT, tags="size_readout",
        )
        bbox = self.canvas.bbox(text_id)
        if bbox:
            bx1, by1, bx2, by2 = bbox
            rect_id = self.canvas.create_rectangle(
                bx1 - 5, by1 - 2, bx2 + 5, by2 + 2,
                fill=SCAN_BG, outline=SCAN_ACCENT, tags="size_readout",
            )
            self.canvas.tag_lower(rect_id, text_id)

    # -------- mouse handlers --------
    def _on_motion(self, event):
        self._update_loupe(event.x, event.y)

    def _on_press(self, event):
        self._start_x = event.x_root
        self._start_y = event.y_root
        if self._rect_id:
            self.canvas.delete(self._rect_id)
        self._rect_id = self.canvas.create_rectangle(
            event.x, event.y, event.x, event.y,
            outline=SCAN_ACCENT, width=2,
        )

    @staticmethod
    def _shift_held(event):
        """0x0001 is the Tk state bit for Shift on Windows."""
        return bool(getattr(event, "state", 0) & 0x0001)

    @staticmethod
    def _enum_window_rects():
        """Return a list of (l, t, r, b) for all visible top-level windows.

        Cached snapshot used for snap-to-edge during selection. Cheap (~5ms)
        and avoids re-querying Win32 from a hot mouse-move handler.
        """
        rects = []
        try:
            user32 = ctypes.windll.user32
            EnumWindowsProc = ctypes.WINFUNCTYPE(
                ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p,
            )

            def cb(hwnd, _lp):
                if not user32.IsWindowVisible(hwnd):
                    return True
                rect = ctypes.wintypes.RECT()
                if not user32.GetWindowRect(hwnd, ctypes.byref(rect)):
                    return True
                w = rect.right - rect.left
                h = rect.bottom - rect.top
                # Skip 0-sized + huge phantom shell windows
                if w < 50 or h < 50 or w > 30000 or h > 30000:
                    return True
                rects.append((rect.left, rect.top, rect.right, rect.bottom))
                return True

            user32.EnumWindows(EnumWindowsProc(cb), 0)
        except Exception:
            log.exception("EnumWindows failed - snap disabled")
        return rects

    def _snap(self, x_root, y_root):
        """Snap (x, y) to nearby window edges within SNAP_THRESHOLD_PX.

        Independent X/Y snapping so we can lock to a left edge while still
        moving freely vertically. O(N) over visible windows; fine for typical
        N=20-40 desktops.
        """
        thr = self.SNAP_THRESHOLD_PX
        snapped_x, snapped_y = x_root, y_root
        best_dx, best_dy = thr + 1, thr + 1
        for l, t, r, b in self._snap_targets:
            for ex in (l, r):
                d = abs(x_root - ex)
                if d < best_dx:
                    best_dx = d
                    snapped_x = ex
            for ey in (t, b):
                d = abs(y_root - ey)
                if d < best_dy:
                    best_dy = d
                    snapped_y = ey
        return snapped_x, snapped_y

    def _apply_constrain(self, x_root, y_root, force_square):
        """If shift is held, force the selection into a square anchored at
        the start point. Returns adjusted (x, y) in screen coords."""
        if not force_square:
            return x_root, y_root
        dx = x_root - self._start_x
        dy = y_root - self._start_y
        size = max(abs(dx), abs(dy))
        nx = self._start_x + (size if dx >= 0 else -size)
        ny = self._start_y + (size if dy >= 0 else -size)
        return nx, ny

    def _adjust_pointer(self, event):
        """Apply snap + shift-constrain in the right order.

        Shift takes precedence (forces square); snap only runs when shift is
        not held so the user can opt out of snap by holding shift.
        """
        if self._shift_held(event):
            return self._apply_constrain(event.x_root, event.y_root, True)
        return self._snap(event.x_root, event.y_root)

    def _on_drag(self, event):
        if not self._rect_id:
            return
        x_root, y_root = self._adjust_pointer(event)
        x1 = min(self._start_x, x_root) - self._vx
        y1 = min(self._start_y, y_root) - self._vy
        x2 = max(self._start_x, x_root) - self._vx
        y2 = max(self._start_y, y_root) - self._vy
        self.canvas.coords(self._rect_id, x1, y1, x2, y2)
        self._update_size_readout(x1, y1, x2, y2)
        self._update_loupe(event.x, event.y)

    def _on_release(self, event):
        x_root, y_root = self._adjust_pointer(event)
        x1 = min(self._start_x, x_root)
        y1 = min(self._start_y, y_root)
        x2 = max(self._start_x, x_root)
        y2 = max(self._start_y, y_root)
        self.destroy()
        if x2 - x1 < 5 or y2 - y1 < 5:
            if self._on_cancel:
                self._on_cancel()
            return
        self.after_idle(lambda: self.on_capture(x1, y1, x2, y2))

    def _cancel(self):
        self.destroy()
        if self._on_cancel:
            self._on_cancel()


# ---------------------------------------------------------------------------
# Main app
# ---------------------------------------------------------------------------
class ScreenshotApp:
    """Toolbar / controller."""

    # 500 ms is plenty for the 'sit above the taskbar' guarantee - we only
    # need to reassert topmost when an app pops up over us, which is itself a
    # rare event. Halving the rate (was 200 ms) cuts idle CPU noticeably with
    # no perceptible difference in user-facing behavior.
    _TOPMOST_INTERVAL_MS = 500

    def __init__(self):
        self.root = tk.Tk()
        self.root.overrideredirect(True)
        self.root.attributes("-topmost", True)

        self.screenshots: list[ScreenshotWindow] = []
        self._counting_down = False
        self._in_tray = False
        self._drag_x = 0
        self._drag_y = 0
        self._delay_menu_visible = False
        self._delay_menu_close_time = 0.0  # set when menu was dismissed by clicking the button
        self._cached_hwnd = None  # populated lazily by _get_hwnd

        self._build_toolbar()
        self._force_topmost()
        self._topmost_loop()
        self._poll_show_trigger()
        # Tray init imports pystray + spins a thread; defer so toolbar paints first
        self.root.after(150, self._setup_tray)
        # First-run welcome (only fires when prefs has no first_run_complete).
        if not prefs.get("first_run_complete"):
            self.root.after(400, self._show_first_run_welcome)

    # ------- UI construction -------
    def _build_toolbar(self):
        # Hairline 1-px border around the toolbar
        border_wrap = tk.Frame(self.root, bg=SCAN_BORDER)
        border_wrap.pack(fill=tk.BOTH, expand=True)
        bar = tk.Frame(border_wrap, bg=SCAN_BG, padx=6, pady=3)
        bar.pack(fill=tk.X, padx=1, pady=1)
        bar.bind("<ButtonPress-1>", self._on_drag_start)
        bar.bind("<B1-Motion>", self._on_drag_move)

        # Title - clickable: shows the About dialog. Drag-to-move still works
        # via the surrounding bar's bindings, so the click+release pattern is
        # the only way the About fires (no accidental open during drag).
        title = tk.Label(
            bar, text=APP_NAME,
            font=("Segoe UI Semibold", 10), fg=SCAN_TEXT, bg=SCAN_BG,
            cursor="hand2",
        )
        title.pack(side=tk.LEFT, padx=(2, 8))
        title.bind("<ButtonPress-1>", self._on_drag_start)
        title.bind("<B1-Motion>", self._on_drag_move)
        title.bind("<ButtonRelease-1>", self._maybe_show_about)

        tk.Frame(bar, bg=SCAN_BORDER, width=1).pack(
            side=tk.LEFT, fill=tk.Y, padx=(0, 6), pady=2
        )

        # Primary action: Capture (only persistent accent in the toolbar)
        self.capture_btn = tk.Button(
            bar, text="Capture", pady=2, padx=10, bd=0,
            font=("Segoe UI", 9), command=self._start_capture,
            relief=tk.FLAT, bg=SCAN_ACCENT, fg=SCAN_ACCENT_FG,
            activebackground=SCAN_ACCENT_HOVER, activeforeground=SCAN_ACCENT_FG,
            cursor="hand2",
        )
        self.capture_btn.pack(side=tk.LEFT, padx=(0, 3))
        self._bind_hover(self.capture_btn, SCAN_ACCENT, SCAN_ACCENT_HOVER)
        # Tooltip doubles as the screenshot-count display (was the
        # standalone "0 / 10" label, now folded in to save toolbar width).
        self._capture_tooltip = Tooltip(
            self.capture_btn, "Capture a region of the screen  \u2013  0 / 10",
        )

        # Delay menu - click-to-toggle dropdown. Restore saved default delay.
        saved_delay = int(prefs.get("last_delay_seconds") or 0)
        self._delay_var = tk.StringVar(value=str(saved_delay))
        self._delay_display = tk.StringVar(
            value="\u25BC" if saved_delay == 0 else f"{saved_delay}s\u25BC"
        )
        self._delay_btn = tk.Button(
            bar, textvariable=self._delay_display,
            pady=2, padx=6, font=("Segoe UI", 9), bd=0,
            relief=tk.FLAT, bg=SCAN_BG, fg=SCAN_TEXT,
            activebackground=SCAN_BTN_HOVER, activeforeground=SCAN_TEXT,
            cursor="hand2", command=self._toggle_delay_menu,
        )
        self._delay_btn.pack(side=tk.LEFT, padx=(0, 3))
        self._bind_hover(self._delay_btn, SCAN_BG, SCAN_BTN_HOVER)

        self._delay_menu = tk.Menu(
            self.root, tearoff=0,
            bg=SCAN_BTN_BG, fg=SCAN_TEXT,
            activebackground=SCAN_ACCENT, activeforeground=SCAN_ACCENT_FG,
            bd=1, relief=tk.FLAT, font=("Segoe UI", 9),
        )
        for sec, label in [(0, "No delay"), (3, "3 sec"), (5, "5 sec"), (10, "10 sec")]:
            self._delay_menu.add_command(
                label=label, command=lambda s=sec: self._set_delay(s)
            )
        self._delay_menu.bind("<Unmap>", self._on_delay_menu_unmap)

        # Close All
        close_all_btn = tk.Button(
            bar, text="Close All", pady=2, padx=10, bd=0,
            font=("Segoe UI", 9), command=self._close_all,
            relief=tk.FLAT, bg=SCAN_BG, fg=SCAN_TEXT,
            activebackground=SCAN_BTN_HOVER, activeforeground=SCAN_TEXT,
            cursor="hand2",
        )
        close_all_btn.pack(side=tk.LEFT, padx=(0, 4))
        self._bind_hover(close_all_btn, SCAN_BG, SCAN_BTN_HOVER)

        # Right-aligned: window controls
        close_btn = tk.Button(
            bar, text="\u2715", pady=2, padx=6, bd=0,
            font=("Segoe UI", 10), command=self._quit,
            relief=tk.FLAT, bg=SCAN_BG, fg=SCAN_MUTED,
            activebackground=SCAN_DANGER, activeforeground="white",
            cursor="hand2",
        )
        close_btn.pack(side=tk.RIGHT, padx=(2, 0))
        self._bind_hover(
            close_btn, SCAN_BG, SCAN_DANGER, fg_normal=SCAN_MUTED, fg_hover="white"
        )

        min_btn = tk.Button(
            bar, text="\u2013", pady=2, padx=6, bd=0,
            font=("Segoe UI", 10), command=self._minimize,
            relief=tk.FLAT, bg=SCAN_BG, fg=SCAN_MUTED,
            activebackground=SCAN_BTN_HOVER, activeforeground=SCAN_TEXT,
            cursor="hand2",
        )
        min_btn.pack(side=tk.RIGHT)
        self._bind_hover(
            min_btn, SCAN_BG, SCAN_BTN_HOVER, fg_normal=SCAN_MUTED, fg_hover=SCAN_TEXT
        )

        # Position window at its natural width (no inflation). Saved x/y
        # restored if available, else top-right with a small margin.
        self.root.update_idletasks()
        natural_w = self.root.winfo_reqwidth()
        natural_h = self.root.winfo_reqheight()
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        sx = prefs.get("toolbar_x")
        sy = prefs.get("toolbar_y")
        if (
            isinstance(sx, int) and isinstance(sy, int)
            and 0 <= sx <= sw - 50 and 0 <= sy <= sh - 30
        ):
            x, y = sx, sy
        else:
            x = sw - natural_w - 20
            y = 20
        self.root.geometry(f"{natural_w}x{natural_h}+{x}+{y}")

    @staticmethod
    def _bind_hover(widget, bg_normal, bg_hover, fg_normal=None, fg_hover=None):
        """Apply a smooth hover effect (Scandinavian flat style)."""
        def on_enter(_e):
            cfg = {"background": bg_hover}
            if fg_hover is not None:
                cfg["foreground"] = fg_hover
            widget.config(**cfg)

        def on_leave(_e):
            cfg = {"background": bg_normal}
            if fg_normal is not None:
                cfg["foreground"] = fg_normal
            widget.config(**cfg)

        widget.bind("<Enter>", on_enter)
        widget.bind("<Leave>", on_leave)

    # ------- topmost & dragging -------
    _SWP_NOMOVE = 0x0002
    _SWP_NOSIZE = 0x0001
    _SWP_SHOWWINDOW = 0x0040
    _HWND_TOPMOST = -1

    def _get_hwnd(self):
        """Resolve and cache the toplevel HWND. Used by every topmost call."""
        if self._cached_hwnd:
            return self._cached_hwnd
        try:
            self._cached_hwnd = int(self.root.frame(), 16)
        except (ValueError, tk.TclError):
            self._cached_hwnd = _U32.GetParent(self.root.winfo_id())
        return self._cached_hwnd

    def _force_topmost(self):
        """Pin the toolbar above the taskbar. Called on UI events that may
        have demoted us (capture finish, tray restore)."""
        try:
            _U32.SetWindowPos(
                self._get_hwnd(), self._HWND_TOPMOST, 0, 0, 0, 0,
                self._SWP_NOMOVE | self._SWP_NOSIZE | self._SWP_SHOWWINDOW,
            )
        except Exception:
            pass
        self.root.attributes("-topmost", True)
        self.root.lift()

    def _topmost_loop(self):
        """Periodic re-assertion of topmost. Lean version that only does the
        SetWindowPos call - the Tk attributes/lift dance we run in
        _force_topmost is a no-op once the window is already topmost, and
        skipping it on every tick noticeably reduces idle wake-ups."""
        if not self.root.winfo_exists():
            return
        if not self._counting_down and not self._in_tray:
            try:
                _U32.SetWindowPos(
                    self._get_hwnd(), self._HWND_TOPMOST, 0, 0, 0, 0,
                    self._SWP_NOMOVE | self._SWP_NOSIZE | self._SWP_SHOWWINDOW,
                )
            except Exception:
                pass
        self.root.after(self._TOPMOST_INTERVAL_MS, self._topmost_loop)

    def _poll_show_trigger(self):
        """A second PinShot launch writes a trigger file; surface ourselves."""
        if not self.root.winfo_exists():
            return
        path = _show_trigger_path()
        if os.path.exists(path):
            try:
                os.unlink(path)
            except OSError:
                pass
            self._restore_from_tray()
            self._force_topmost()
        self.root.after(800, self._poll_show_trigger)

    def _maybe_show_about(self, event):
        """Open the About dialog only on a clean click, not after a drag."""
        # If the cursor moved >3px between press and release, it was a drag.
        moved = abs(event.x_root - self.root.winfo_x() - self._drag_x) + \
                abs(event.y_root - self.root.winfo_y() - self._drag_y)
        if moved > 3:
            return
        self._show_about_dialog()

    def _show_about_dialog(self):
        win = tk.Toplevel(self.root)
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        win.configure(bg=SCAN_BORDER)
        inner = tk.Frame(win, bg=SCAN_BG, padx=24, pady=18)
        inner.pack(padx=1, pady=1)
        tk.Label(
            inner, text=APP_NAME,
            font=("Segoe UI Semibold", 16), fg=SCAN_TEXT, bg=SCAN_BG,
        ).pack(anchor="w")
        tk.Label(
            inner, text=f"Version {APP_VERSION}",
            font=("Segoe UI", 9), fg=SCAN_MUTED, bg=SCAN_BG,
        ).pack(anchor="w", pady=(0, 10))
        tk.Label(
            inner, text="Free \u2013 no license required",
            font=("Segoe UI Semibold", 10), fg=SCAN_ACCENT, bg=SCAN_BG,
        ).pack(anchor="w")
        tk.Label(
            inner,
            text=f"Designed and developed by {APP_AUTHOR}.\nFor support, contact {APP_AUTHOR}.",
            font=("Segoe UI", 9), fg=SCAN_TEXT, bg=SCAN_BG,
            justify="left",
        ).pack(anchor="w", pady=(8, 14))
        tk.Label(
            inner,
            text=f"{APP_COPYRIGHT}\nSignature: {APP_PROVENANCE_ID}",
            font=("Segoe UI", 8), fg=SCAN_MUTED, bg=SCAN_BG,
            justify="left",
        ).pack(anchor="w", pady=(0, 14))
        tk.Button(
            inner, text="Close", padx=18, pady=4, bd=0, relief=tk.FLAT,
            font=("Segoe UI", 9), bg=SCAN_ACCENT, fg=SCAN_ACCENT_FG,
            activebackground=SCAN_ACCENT_HOVER, activeforeground=SCAN_ACCENT_FG,
            cursor="hand2", command=win.destroy,
        ).pack(anchor="e")
        win.update_idletasks()
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        ww = win.winfo_reqwidth()
        wh = win.winfo_reqheight()
        win.geometry(f"+{(sw - ww) // 2}+{(sh - wh) // 2}")

    def _show_first_run_welcome(self):
        """Tiny opt-in greeting on the very first launch."""
        if not self.root.winfo_exists():
            return
        prefs.set("first_run_complete", True)
        win = tk.Toplevel(self.root)
        win.overrideredirect(True)
        win.attributes("-topmost", True)
        win.configure(bg=SCAN_BORDER)
        inner = tk.Frame(win, bg=SCAN_BG, padx=20, pady=16)
        inner.pack(padx=1, pady=1)
        tk.Label(
            inner, text=f"Welcome to {APP_NAME}",
            font=("Segoe UI Semibold", 12), fg=SCAN_TEXT, bg=SCAN_BG,
        ).pack(anchor="w")
        tk.Label(
            inner,
            text=(
                "  - Click 'Capture' to grab a region of the screen\n"
                "  - Right-click any pinned screenshot to mark it up\n"
                "  - Drag the toolbar to reposition it - PinShot remembers it"
            ),
            font=("Segoe UI", 9), fg=SCAN_TEXT, bg=SCAN_BG,
            justify="left", anchor="w",
        ).pack(anchor="w", pady=(8, 12))
        tk.Button(
            inner, text="Got it", padx=18, pady=4, bd=0, relief=tk.FLAT,
            font=("Segoe UI", 9), bg=SCAN_ACCENT, fg=SCAN_ACCENT_FG,
            activebackground=SCAN_ACCENT_HOVER, activeforeground=SCAN_ACCENT_FG,
            cursor="hand2", command=win.destroy,
        ).pack(anchor="e")
        win.update_idletasks()
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        ww = win.winfo_reqwidth()
        wh = win.winfo_reqheight()
        win.geometry(f"+{(sw - ww) // 2}+{(sh - wh) // 2}")

    def _on_drag_start(self, event):
        self._drag_x = event.x_root - self.root.winfo_x()
        self._drag_y = event.y_root - self.root.winfo_y()

    def _on_drag_move(self, event):
        x = event.x_root - self._drag_x
        y = event.y_root - self._drag_y
        self.root.geometry(f"+{x}+{y}")
        # Persist position - throttled write because Prefs.set is a no-op when
        # the value is unchanged, so coordinate-jitter won't spam the disk.
        prefs.update(toolbar_x=x, toolbar_y=y)

    # ------- system tray -------
    def _setup_tray(self):
        try:
            import pystray
        except ImportError:
            return
        ico_path = _asset_path("pinshot.ico")
        _ensure_pil()
        try:
            tray_image = Image.open(ico_path)
        except Exception:
            tray_image = Image.new("RGB", (32, 32), SCAN_ACCENT)
        menu = pystray.Menu(
            pystray.MenuItem(
                "Capture", lambda: self.root.after(0, self._start_capture)
            ),
            pystray.MenuItem(
                "Show PinShot",
                lambda: self.root.after(0, self._restore_from_tray),
                default=True,
            ),
            pystray.MenuItem("Quit", lambda: self.root.after(0, self._quit)),
        )
        self._tray_icon = pystray.Icon(APP_NAME, tray_image, APP_NAME, menu)
        threading.Thread(target=self._tray_icon.run, daemon=True).start()

    def _minimize(self):
        self._in_tray = True
        self.root.withdraw()

    def _restore_from_tray(self):
        self._in_tray = False
        self.root.deiconify()
        self.root.overrideredirect(True)
        self._force_topmost()

    # ------- status & delay -------
    def _update_status(self):
        count = len(self.screenshots)
        self.capture_btn.config(
            state=tk.NORMAL if count < MAX_SCREENSHOTS else tk.DISABLED
        )
        # Count is shown via the Capture-button tooltip rather than a
        # standalone "0 / 10" label - keeps the toolbar compact.
        self._capture_tooltip.text = (
            f"Capture a region of the screen  \u2013  {count} / {MAX_SCREENSHOTS}"
        )

    def _set_delay(self, seconds):
        self._delay_var.set(str(seconds))
        self._delay_display.set("\u25BC" if seconds == 0 else f"{seconds}s\u25BC")
        prefs.set("last_delay_seconds", seconds)

    def _toggle_delay_menu(self):
        """Show the delay menu, or hide it if it's already showing."""
        # Suppress the immediate reopen that can happen when the user clicks
        # the button to dismiss the menu and Tk delivers the click to us too.
        if time.monotonic() - self._delay_menu_close_time < 0.25:
            return
        if self._delay_menu_visible:
            try:
                self._delay_menu.unpost()
            except tk.TclError:
                pass
            self._delay_menu_visible = False
            return
        x = self._delay_btn.winfo_rootx()
        y = self._delay_btn.winfo_rooty() + self._delay_btn.winfo_height()
        self._delay_menu_visible = True
        self._delay_menu.tk_popup(x, y)

    def _on_delay_menu_unmap(self, _event):
        """Track external dismissals (Esc, click outside, item selected)."""
        self._delay_menu_visible = False
        # If the cursor sits over the toggle button right now, the user
        # clicked it to close - record that so the button's command callback
        # doesn't immediately reopen the menu.
        try:
            bx = self._delay_btn.winfo_rootx()
            by = self._delay_btn.winfo_rooty()
            bw = self._delay_btn.winfo_width()
            bh = self._delay_btn.winfo_height()
            px = self._delay_btn.winfo_pointerx()
            py = self._delay_btn.winfo_pointery()
            if bx <= px < bx + bw and by <= py < by + bh:
                self._delay_menu_close_time = time.monotonic()
        except tk.TclError:
            pass

    # ------- capture flow -------
    def _start_capture(self):
        if len(self.screenshots) >= MAX_SCREENSHOTS:
            messagebox.showwarning(
                "Limit Reached",
                f"Maximum of {MAX_SCREENSHOTS} screenshots reached.\n"
                "Close an existing one first.",
            )
            return
        delay = int(self._delay_var.get())
        if delay > 0:
            self._start_countdown(delay)
        else:
            if not self._in_tray:
                self.root.withdraw()
            self.root.after(300, self._show_overlay)

    def _start_countdown(self, seconds):
        self._counting_down = True
        if not self._in_tray:
            self.root.withdraw()
        self._countdown_win = tk.Toplevel(self.root)
        self._countdown_win.overrideredirect(True)
        self._countdown_win.attributes("-topmost", True)
        self._countdown_win.attributes("-alpha", 0.95)
        self._countdown_win.configure(bg=SCAN_ACCENT)
        size = 140
        sx = self.root.winfo_screenwidth() // 2 - size // 2
        sy = self.root.winfo_screenheight() // 2 - size // 2
        self._countdown_win.geometry(f"{size}x{size}+{sx}+{sy}")
        cd_inner = tk.Frame(self._countdown_win, bg=SCAN_BG)
        cd_inner.pack(fill=tk.BOTH, expand=True, padx=2, pady=2)
        self._cd_label = tk.Label(
            cd_inner, text=str(seconds),
            font=("Segoe UI", 48, "bold"), fg=SCAN_ACCENT, bg=SCAN_BG,
        )
        self._cd_label.pack(expand=True)
        tk.Label(
            cd_inner, text="press Esc to cancel",
            font=("Segoe UI", 8), fg=SCAN_MUTED, bg=SCAN_BG,
        ).pack(side=tk.BOTTOM, pady=(0, 6))
        self._countdown_win.bind("<Escape>", lambda e: self._cancel_countdown())
        self._countdown_win.focus_force()
        self._cd_remaining = seconds
        self._tick_countdown()

    def _tick_countdown(self):
        if self._cd_remaining <= 0:
            self._counting_down = False
            self._countdown_win.destroy()
            _ensure_pil()
            try:
                frozen = ImageGrab.grab(all_screens=True)
            except Exception:
                frozen = None
            self.root.after(50, lambda: self._show_overlay(frozen_image=frozen))
            return
        self._cd_label.config(text=str(self._cd_remaining))
        self._cd_remaining -= 1
        self._countdown_win.after(1000, self._tick_countdown)

    def _cancel_countdown(self):
        self._counting_down = False
        if hasattr(self, "_countdown_win") and self._countdown_win.winfo_exists():
            self._countdown_win.destroy()
        if not self._in_tray:
            self.root.deiconify()
            self._force_topmost()

    def _show_overlay(self, frozen_image=None):
        if frozen_image is not None:
            on_capture = lambda x1, y1, x2, y2: self._do_capture(
                x1, y1, x2, y2, frozen_image
            )
        else:
            on_capture = self._do_capture
        SelectionOverlay(
            self.root, on_capture, self._on_capture_cancel,
            freeze_image=frozen_image,
        )

    def _on_capture_cancel(self):
        if not self._in_tray:
            self.root.deiconify()
            self._force_topmost()

    def _do_capture(self, x1, y1, x2, y2, frozen_image=None):
        _ensure_pil()
        try:
            if frozen_image is not None:
                vx, vy, _, _ = _virtual_screen_rect()
                img = frozen_image.crop((x1 - vx, y1 - vy, x2 - vx, y2 - vy))
            else:
                img = ImageGrab.grab(bbox=(x1, y1, x2, y2), all_screens=True)
        except Exception as e:
            if not self._in_tray:
                self.root.deiconify()
            messagebox.showerror("Capture Error", str(e))
            return

        if not self._in_tray:
            self.root.deiconify()
            self._force_topmost()

        used = {win.index for win in self.screenshots}
        idx = next(i for i in range(1, MAX_SCREENSHOTS + 1) if i not in used)
        win = ScreenshotWindow(
            self.root, img, idx, self._on_screenshot_close
        )
        # Don't force window.geometry() here - the canvas inside the window
        # is already sized to the image's exact pixel dimensions in
        # ScreenshotWindow.__init__, so Tk auto-sizes the Toplevel around it.
        # Forcing window geometry to image size would shrink the canvas by the
        # title-bar / border height and cause the first render to be
        # downsampled (the source of the "blurry" appearance).
        self.screenshots.append(win)
        self._update_status()
        win._copy_to_clipboard()

    def _on_screenshot_close(self, win: ScreenshotWindow):
        if win in self.screenshots:
            self.screenshots.remove(win)
        self._update_status()

    def _close_all(self):
        for win in list(self.screenshots):
            win.destroy()
        self.screenshots.clear()
        self._update_status()

    def _quit(self):
        self._close_all()
        if hasattr(self, "_tray_icon"):
            try:
                self._tray_icon.stop()
            except Exception:
                pass
        self.root.destroy()
        sys.exit(0)

    def run(self):
        self.root.mainloop()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    _setup_logging()
    _install_excepthook()
    try:
        ctypes.windll.shcore.SetProcessDpiAwareness(2)
    except Exception as e:
        log.warning("SetProcessDpiAwareness failed: %s", e)
    if not _acquire_single_instance():
        log.info("Another PinShot instance detected - exiting silently.")
        sys.exit(0)
    try:
        ScreenshotApp().run()
    except Exception:
        log.exception("Fatal error in main loop")
        raise
