"""Win32 helpers: admin check, desktop (WorkerW) attachment, wallpaper, cursor."""
from __future__ import annotations

import ctypes
import os
from ctypes import wintypes

user32 = ctypes.WinDLL("user32", use_last_error=True)
kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
shell32 = ctypes.WinDLL("shell32", use_last_error=True)
gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)

user32.GetDC.restype = wintypes.HDC
user32.GetDC.argtypes = [wintypes.HWND]
user32.ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]
user32.PrintWindow.argtypes = [wintypes.HWND, wintypes.HDC, wintypes.UINT]

gdi32.CreateCompatibleDC.restype = wintypes.HDC
gdi32.CreateCompatibleDC.argtypes = [wintypes.HDC]
gdi32.CreateCompatibleBitmap.restype = wintypes.HBITMAP
gdi32.CreateCompatibleBitmap.argtypes = [wintypes.HDC, ctypes.c_int, ctypes.c_int]
gdi32.SelectObject.restype = wintypes.HGDIOBJ
gdi32.SelectObject.argtypes = [wintypes.HDC, wintypes.HGDIOBJ]
gdi32.SetViewportOrgEx.argtypes = [
    wintypes.HDC,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.POINTER(wintypes.POINT),
]
gdi32.GetDIBits.argtypes = [
    wintypes.HDC,
    wintypes.HBITMAP,
    wintypes.UINT,
    wintypes.UINT,
    ctypes.c_void_p,
    ctypes.c_void_p,
    wintypes.UINT,
]
gdi32.DeleteObject.argtypes = [wintypes.HGDIOBJ]
gdi32.DeleteDC.argtypes = [wintypes.HDC]
gdi32.BitBlt.argtypes = [
    wintypes.HDC,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    ctypes.c_int,
    wintypes.HDC,
    ctypes.c_int,
    ctypes.c_int,
    wintypes.DWORD,
]

HWND_BOTTOM = 1
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
SWP_NOOWNERZORDER = 0x0200
SMTO_NORMAL = 0x0000


def is_admin() -> bool:
    try:
        return bool(shell32.IsUserAnAdmin())
    except Exception:
        return False


def _enum_top_windows():
    hwnds = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _lparam):
        hwnds.append(hwnd)
        return True

    user32.EnumWindows(cb, 0)
    return hwnds


def _class_name(hwnd) -> str:
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def find_desktop_worker():
    """Return the WorkerW window that sits directly above the wallpaper."""
    progman = user32.FindWindowW("Progman", None)
    if progman:
        result = wintypes.DWORD()
        # 0x052C makes Explorer create the WorkerW used for wallpaper hosting.
        user32.SendMessageTimeoutW(
            progman, 0x052C, 0, 0, SMTO_NORMAL, 1000, ctypes.byref(result)
        )

    defview_parent = None
    for hwnd in _enum_top_windows():
        if user32.FindWindowExW(hwnd, 0, "SHELLDLL_DefView", None):
            defview_parent = hwnd
            break

    if defview_parent:
        worker = user32.FindWindowExW(0, defview_parent, "WorkerW", None)
        if worker:
            return worker
        # Older layouts keep the view inside Progman itself.
        if progman and defview_parent == progman:
            worker = user32.FindWindowExW(0, progman, "WorkerW", None)
            if worker:
                return worker
    return progman


def attach_to_desktop(hwnd: int) -> int | None:
    worker = find_desktop_worker()
    if not worker:
        return None
    if user32.GetParent(hwnd) == worker:
        return worker
    user32.SetParent(hwnd, worker)
    return worker


def detach_from_desktop(hwnd: int) -> None:
    user32.SetParent(hwnd, 0)


def parent_client_origin(hwnd: int) -> tuple[int, int]:
    parent = user32.GetParent(hwnd)
    if not parent:
        return 0, 0
    pt = wintypes.POINT(0, 0)
    user32.ClientToScreen(parent, ctypes.byref(pt))
    return pt.x, pt.y


def move_window(hwnd: int, x: int, y: int, w: int, h: int) -> None:
    """Place the window at screen coordinates (x, y)."""
    px, py = parent_client_origin(hwnd)
    user32.SetWindowPos(
        hwnd,
        0,
        x - px,
        y - py,
        w,
        h,
        SWP_NOZORDER | SWP_NOACTIVATE | SWP_NOOWNERZORDER,
    )


def window_rect(hwnd: int) -> tuple[int, int, int, int]:
    rect = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    return rect.left, rect.top, rect.right - rect.left, rect.bottom - rect.top


def send_to_bottom(hwnd: int) -> None:
    """Keep the widget just above the desktop, below every other window."""
    progman = user32.FindWindowW("Progman", None)
    insert_after = progman if progman and progman != hwnd else HWND_BOTTOM
    user32.SetWindowPos(
        hwnd,
        insert_after,
        0,
        0,
        0,
        0,
        SWP_NOMOVE | SWP_NOSIZE | SWP_NOACTIVATE | SWP_NOOWNERZORDER,
    )


def cursor_pos() -> tuple[int, int]:
    pt = wintypes.POINT()
    user32.GetCursorPos(ctypes.byref(pt))
    return pt.x, pt.y


def mouse_buttons_down() -> bool:
    """True while any physical mouse button is held (works without focus)."""
    for key in (0x01, 0x02, 0x04):  # left, right, middle
        if user32.GetAsyncKeyState(key) & 0x8000:
            return True
    return False


def is_our_window(hwnd: int) -> bool:
    return user32.GetParent(hwnd) != 0


def wallpaper_path() -> str | None:
    override = os.environ.get("LGS_WALLPAPER")
    if override and os.path.exists(override):
        return override
    themes = os.path.join(os.environ["APPDATA"], "Microsoft", "Windows", "Themes")
    transcoded = os.path.join(themes, "TranscodedWallpaper")
    if os.path.exists(transcoded):
        return transcoded
    buf = ctypes.create_unicode_buffer(1024)
    if user32.SystemParametersInfoW(0x0073, 1024, buf, 0) and buf.value:
        return buf.value
    return None


def capture_desktop_region(x: int, y: int, w: int, h: int):
    """Grab a slice of the desktop (wallpaper included) even under our window."""
    from PySide6.QtGui import QImage

    progman = user32.FindWindowW("Progman", None)
    if not progman:
        return None
    rect = wintypes.RECT()
    user32.GetWindowRect(progman, ctypes.byref(rect))
    if rect.right - rect.left <= 0 or rect.bottom - rect.top <= 0:
        return None

    hdc_screen = user32.GetDC(0)
    if not hdc_screen:
        return None
    hdc_mem = gdi32.CreateCompatibleDC(hdc_screen)
    bmp = gdi32.CreateCompatibleBitmap(hdc_screen, w, h)
    old = gdi32.SelectObject(hdc_mem, bmp)

    # Shift the window origin so PrintWindow paints the requested slice.
    gdi32.SetViewportOrgEx(hdc_mem, -x, -y, None)
    ok = user32.PrintWindow(progman, hdc_mem, 2)  # PW_RENDERFULLCONTENT
    gdi32.SetViewportOrgEx(hdc_mem, 0, 0, None)

    img = None
    if ok:
        class BITMAPINFOHEADER(ctypes.Structure):
            _fields_ = [
                ("biSize", wintypes.DWORD),
                ("biWidth", wintypes.LONG),
                ("biHeight", wintypes.LONG),
                ("biPlanes", wintypes.WORD),
                ("biBitCount", wintypes.WORD),
                ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD),
                ("biXPelsPerMeter", wintypes.LONG),
                ("biYPelsPerMeter", wintypes.LONG),
                ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD),
            ]

        bi = BITMAPINFOHEADER()
        bi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bi.biWidth = w
        bi.biHeight = -h
        bi.biPlanes = 1
        bi.biBitCount = 32
        bi.biCompression = 0
        buf = ctypes.create_string_buffer(w * h * 4)
        if gdi32.GetDIBits(hdc_mem, bmp, 0, h, buf, ctypes.byref(bi), 0) == h:
            img = QImage(buf, w, h, QImage.Format_ARGB32).copy()

    gdi32.SelectObject(hdc_mem, old)
    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(hdc_mem)
    user32.ReleaseDC(0, hdc_screen)
    return img


def find_live_wallpaper_window(x: int, y: int) -> int:
    """Find a Wallpaper Engine surface that covers the given screen point."""
    progman = user32.FindWindowW("Progman", None)
    if not progman:
        return 0

    found = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def cb(hwnd, _lparam):
        cls = _class_name(hwnd)
        if not cls.startswith("WPE") or not user32.IsWindowVisible(hwnd):
            return True
        rect = wintypes.RECT()
        user32.GetWindowRect(hwnd, ctypes.byref(rect))
        w, h = rect.right - rect.left, rect.bottom - rect.top
        if w < 64 or h < 64:
            return True
        if rect.left <= x < rect.right and rect.top <= y < rect.bottom:
            found.append((w * h, hwnd))
        return True

    user32.EnumChildWindows(progman, cb, 0)
    if not found:
        return 0
    found.sort()
    return found[0][1]


def capture_window_region(hwnd: int, x: int, y: int, w: int, h: int):
    """Read a rectangle (screen coordinates) from a window's own DC.

    Wallpaper Engine renders into its own windows, so this gives the live
    wallpaper without ever capturing our widget or other windows.
    """
    from PySide6.QtGui import QImage

    rect = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    sx, sy = x - rect.left, y - rect.top

    hdc_screen = user32.GetDC(0)
    if not hdc_screen:
        return None
    hdc_mem = gdi32.CreateCompatibleDC(hdc_screen)
    bmp = gdi32.CreateCompatibleBitmap(hdc_screen, w, h)
    old = gdi32.SelectObject(hdc_mem, bmp)
    ok = False
    hdc_src = user32.GetDC(hwnd)
    if hdc_src:
        ok = gdi32.BitBlt(hdc_mem, 0, 0, w, h, hdc_src, sx, sy, 0x00CC0020)
        user32.ReleaseDC(hwnd, hdc_src)

    img = None
    if ok:
        class BITMAPINFOHEADER(ctypes.Structure):
            _fields_ = [
                ("biSize", wintypes.DWORD),
                ("biWidth", wintypes.LONG),
                ("biHeight", wintypes.LONG),
                ("biPlanes", wintypes.WORD),
                ("biBitCount", wintypes.WORD),
                ("biCompression", wintypes.DWORD),
                ("biSizeImage", wintypes.DWORD),
                ("biXPelsPerMeter", wintypes.LONG),
                ("biYPelsPerMeter", wintypes.LONG),
                ("biClrUsed", wintypes.DWORD),
                ("biClrImportant", wintypes.DWORD),
            ]

        bi = BITMAPINFOHEADER()
        bi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
        bi.biWidth = w
        bi.biHeight = -h
        bi.biPlanes = 1
        bi.biBitCount = 32
        bi.biCompression = 0
        buf = ctypes.create_string_buffer(w * h * 4)
        if gdi32.GetDIBits(hdc_mem, bmp, 0, h, buf, ctypes.byref(bi), 0) == h:
            img = QImage(buf, w, h, QImage.Format_ARGB32).copy()

    gdi32.SelectObject(hdc_mem, old)
    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(hdc_mem)
    user32.ReleaseDC(0, hdc_screen)
    return img


def set_no_activate(hwnd: int) -> None:
    """Keep the widget from stealing focus when clicked."""
    GWL_EXSTYLE = -20
    WS_EX_NOACTIVATE = 0x08000000
    style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
    user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style | WS_EX_NOACTIVATE)


def set_click_through(hwnd: int, enabled: bool) -> None:
    GWL_EXSTYLE = -20
    WS_EX_TRANSPARENT = 0x00000020
    WS_EX_LAYERED = 0x00080000
    style = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
    if enabled:
        style |= WS_EX_TRANSPARENT | WS_EX_LAYERED
    else:
        style &= ~WS_EX_TRANSPARENT
    user32.SetWindowLongW(hwnd, GWL_EXSTYLE, style)
