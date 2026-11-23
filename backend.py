"""Qt bridge: sensor data, wallpaper glass texture, window behaviour, tray state."""
from __future__ import annotations

import os
import time
from collections import deque

from PySide6.QtCore import Property, QObject, Qt, QTimer, Signal, Slot
from PySide6.QtGui import QImage
from PySide6.QtQuick import QQuickImageProvider

import config
import logutil
import winutil
from sensors import SensorWorker

ROOT = os.path.dirname(os.path.abspath(__file__))
HISTORY = 48
BACKDROP_PAD = 80
BLUR_RADIUS = 26.0
BACKDROP_DOWNSCALE = 3
# A static wallpaper only needs to be re-read when something actually changes.
STATIC_REFRESH = 5.0


class BackdropProvider(QQuickImageProvider):
    """Serves the freshly captured desktop slice to QML without disk I/O."""

    def __init__(self):
        super().__init__(QQuickImageProvider.Image)
        self.image = QImage()

    def requestImage(self, image_id, size, requested_size):
        if not self.image.isNull():
            size.setWidth(self.image.width())
            size.setHeight(self.image.height())
            return self.image
        return QImage()


def _blur(img: QImage, radius: int) -> QImage:
    """Cheap frosted blur: shrink (averages pixels), then scale back up."""
    if radius <= 1 or img.isNull():
        return img
    factor = max(2, int(radius))
    small = img.scaled(
        max(1, img.width() // factor),
        max(1, img.height() // factor),
        Qt.IgnoreAspectRatio,
        Qt.SmoothTransformation,
    )
    return small.scaled(
        img.width(), img.height(), Qt.IgnoreAspectRatio, Qt.SmoothTransformation
    )


def _is_black(img: QImage) -> bool:
    """Detect a failed capture (D3D/HDR surfaces sometimes read back as black)."""
    w, h = img.width(), img.height()
    if w < 4 or h < 4:
        return True
    brightest = 0
    for i in range(1, 6):
        for j in range(1, 6):
            color = img.pixelColor(w * i // 6, h * j // 6)
            brightest = max(brightest, color.red(), color.green(), color.blue())
    return brightest < 8


class Backend(QObject):
    sampleTick = Signal()
    fansChanged = Signal()
    stateChanged = Signal()
    menuRequested = Signal()
    menuDismissed = Signal()
    visibilityRequested = Signal()
    resetRequested = Signal()
    geometryChanged = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._cfg = config.load()
        self._window = None
        self._active = False
        self._win_x = 0
        self._win_y = 0
        self._drag_cursor = (0, 0)
        self._drag_win = (0, 0)

        self._cpu_temp = None
        self._cpu_load = None
        self._cpu_power = None
        self._fan_pct = None
        self._fan_rpm = None
        self._fan_name = self._cfg.get("fan_sensor") or "CPU Fan"
        self._gpu_temp = None
        self._gpu_load = None
        self._gpu_power = None
        self._gpu_fan = None

        self._hist_cpu = deque([None] * HISTORY, maxlen=HISTORY)
        self._hist_fan = deque([None] * HISTORY, maxlen=HISTORY)
        self._hist_gpu = deque([None] * HISTORY, maxlen=HISTORY)

        self._wp_x = 0
        self._wp_y = 0
        self._wp_w = 1920
        self._wp_h = 1080
        self._dpr = 1.0
        self._backdrop = QImage()
        self._backdrop_rev = 0
        self._backdrop_source = ""
        self._backdrop_logged = ""
        self._wp_hwnd = 0
        self._wp_mirror = 0
        self._wp_retry = 0.0
        self._wall_key = None
        self._wall_image = None
        self._static_geom = None
        self._last_capture = 0.0
        self._locked = bool(self._cfg.get("locked", False))
        self._autostart = config.autostart_enabled()
        self.provider = BackdropProvider()
        self._dark = self._detect_dark()
        self._refresh_theme()

        self._fans: list[dict] = []
        self._admin = winutil.is_admin()
        self._error = ""

        self._worker = SensorWorker(self._fan_name, 1000)
        self._worker.sample.connect(self._on_sample)
        self._worker.inventory.connect(self._on_inventory)
        self._worker.failed.connect(self._on_error)

        self._theme_timer = QTimer(self)
        self._theme_timer.setInterval(15000)
        self._theme_timer.timeout.connect(self._refresh_theme)
        self._theme_timer.start()

        self._bottom_timer = QTimer(self)
        self._bottom_timer.setInterval(1200)
        self._bottom_timer.timeout.connect(self._keep_bottom)
        self._bottom_timer.start()

        self._wall_timer = QTimer(self)
        self._wall_timer.setInterval(50)
        self._wall_timer.timeout.connect(self.refresh_backdrop)
        self._wall_timer.start()

    # ------------------------------------------------------------------ setup
    def start(self):
        if os.environ.get("LGS_DEMO"):
            self._fans = [
                {"name": "CPU Fan", "rpm": 2100, "pct": 74},
                {"name": "Pump Fan", "rpm": 1800, "pct": 60},
                {"name": "System Fan #1", "rpm": 900, "pct": 40},
                {"name": "System Fan #2", "rpm": 0, "pct": 0},
            ]
            self.fansChanged.emit()
            self._demo_t = 0.0
            self._demo_timer = QTimer(self)
            self._demo_timer.setInterval(1000)
            self._demo_timer.timeout.connect(self._demo_tick)
            self._demo_timer.start()
            return
        self._worker.start()

    def _demo_tick(self):
        import math

        self._demo_t += 0.35
        t = self._demo_t
        cpu = 58 + 9 * math.sin(t) + 2 * math.sin(t * 3.1)
        fan = 62 + 14 * math.sin(t * 0.8 + 1.0)
        gpu = 46 + 6 * math.sin(t * 1.3 + 2.0)
        self._on_sample(
            {
                "cpu": {"temp": cpu, "load": 28 + 12 * math.sin(t * 1.7), "power": 62},
                "fan": {"name": "CPU Fan", "rpm": 1800 + 300 * math.sin(t * 0.8 + 1.0), "pct": fan},
                "gpu": {"temp": gpu, "load": 30 + 20 * math.sin(t * 1.1), "power": 74, "fan": 0},
            }
        )

    def attach_window(self, window):
        self._window = window
        self._sync_geometry()
        try:
            screen = window.screen()
            self.update_screen(
                screen.geometry().x(),
                screen.geometry().y(),
                screen.geometry().width(),
                screen.geometry().height(),
                screen.devicePixelRatio(),
            )
        except Exception:
            pass

    def _sync_geometry(self):
        if not self._window:
            return
        x, y = self._window.x(), self._window.y()
        if (x, y) != (self._win_x, self._win_y):
            self._win_x, self._win_y = x, y
            self.geometryChanged.emit()

    @Property(int, notify=geometryChanged)
    def winX(self):
        return self._win_x

    @Property(int, notify=geometryChanged)
    def winY(self):
        return self._win_y

    @Slot()
    def drag_begin(self):
        if not self._window:
            return
        self._drag_cursor = winutil.cursor_pos()
        self._drag_win = (self._window.x(), self._window.y())

    @Slot()
    def drag_update(self):
        if not self._window:
            return
        cx, cy = winutil.cursor_pos()
        self._window.setPosition(
            self._drag_win[0] + cx - self._drag_cursor[0],
            self._drag_win[1] + cy - self._drag_cursor[1],
        )
        self._sync_geometry()
        self.refresh_backdrop()

    @Slot()
    def drag_end(self):
        if not self._window:
            return
        self._sync_geometry()
        self.save_position(self._window.x(), self._window.y())

    @Slot(bool)
    def set_active(self, active: bool):
        self._active = active

    @Slot()
    def enable_no_activate(self):
        if not self._window:
            return
        try:
            winutil.set_no_activate(int(self._window.winId()))
        except Exception:
            pass

    def _keep_bottom(self):
        if not self._window:
            return
        try:
            self._sync_geometry()
            hwnd = int(self._window.winId())
            if winutil.user32.IsIconic(hwnd):
                winutil.user32.ShowWindow(hwnd, 4)  # SW_SHOWNOACTIVATE
            if not self._active:
                winutil.send_to_bottom(hwnd)
        except Exception:
            pass

    # ------------------------------------------------------------------ data
    def _on_inventory(self, fans: list):
        self._fans = fans
        self.fansChanged.emit()

    def _on_error(self, message: str):
        self._error = message

    def _on_sample(self, data: dict):
        cpu = data.get("cpu") or {}
        fan = data.get("fan") or {}
        gpu = data.get("gpu") or {}

        self._cpu_temp = cpu.get("temp")
        self._cpu_load = cpu.get("load")
        self._cpu_power = cpu.get("power")
        self._fan_pct = fan.get("pct")
        self._fan_rpm = fan.get("rpm")
        if fan.get("name"):
            self._fan_name = fan["name"]
        self._gpu_temp = gpu.get("temp")
        self._gpu_load = gpu.get("load")
        self._gpu_power = gpu.get("power")
        self._gpu_fan = gpu.get("fan")

        self._hist_cpu.append(self._cpu_temp)
        self._hist_fan.append(self._fan_pct)
        self._hist_gpu.append(self._gpu_temp)
        self.sampleTick.emit()

    # ------------------------------------------------------------------ props
    @Property(float, notify=sampleTick)
    def cpuTemp(self):
        return self._cpu_temp if self._cpu_temp is not None else -1.0

    @Property(float, notify=sampleTick)
    def cpuLoad(self):
        return self._cpu_load if self._cpu_load is not None else -1.0

    @Property(float, notify=sampleTick)
    def cpuPower(self):
        return self._cpu_power if self._cpu_power is not None else -1.0

    @Property(float, notify=sampleTick)
    def fanPct(self):
        return self._fan_pct if self._fan_pct is not None else -1.0

    @Property(float, notify=sampleTick)
    def fanRpm(self):
        return self._fan_rpm if self._fan_rpm is not None else -1.0

    @Property(str, notify=sampleTick)
    def fanName(self):
        return self._fan_name

    @Property(float, notify=sampleTick)
    def gpuTemp(self):
        return self._gpu_temp if self._gpu_temp is not None else -1.0

    @Property(float, notify=sampleTick)
    def gpuLoad(self):
        return self._gpu_load if self._gpu_load is not None else -1.0

    @Property(float, notify=sampleTick)
    def gpuPower(self):
        return self._gpu_power if self._gpu_power is not None else -1.0

    @Property(float, notify=sampleTick)
    def gpuFan(self):
        return self._gpu_fan if self._gpu_fan is not None else -1.0

    @Property(list, notify=sampleTick)
    def cpuHistory(self):
        return list(self._hist_cpu)

    @Property(list, notify=sampleTick)
    def fanHistory(self):
        return list(self._hist_fan)

    @Property(list, notify=sampleTick)
    def gpuHistory(self):
        return list(self._hist_gpu)

    @Property(bool, constant=True)
    def isAdmin(self):
        return self._admin

    @Property(bool, notify=sampleTick)
    def hasData(self):
        return self._cpu_temp is not None

    @Property(str, notify=stateChanged)
    def errorText(self):
        return self._error

    @Property(bool, notify=stateChanged)
    def darkTheme(self):
        return self._dark

    @Property(bool, notify=stateChanged)
    def locked(self):
        return self._locked

    @Slot(bool)
    def set_locked(self, locked: bool):
        self._locked = bool(locked)
        self._cfg["locked"] = self._locked
        config.save(self._cfg)
        self.stateChanged.emit()

    @Property(bool, notify=stateChanged)
    def autostart(self):
        return self._autostart

    @Slot(bool)
    def set_autostart(self, enabled: bool):
        config.set_autostart(bool(enabled))
        self._autostart = config.autostart_enabled()
        self._cfg["autostart"] = self._autostart
        config.save(self._cfg)
        self.stateChanged.emit()

    @Property(str, notify=stateChanged)
    def themeMode(self):
        return self._cfg.get("theme", "auto")

    # -------------------------------------------------------------- menu calls
    @Slot()
    def dismiss_menu(self):
        self.menuDismissed.emit()

    @Slot()
    def request_toggle_visibility(self):
        self.visibilityRequested.emit()

    @Slot()
    def request_reset_position(self):
        self.resetRequested.emit()

    @Slot()
    def restart_elevated_app(self):
        config.restart_elevated()

    @Slot()
    def quit_app(self):
        from PySide6.QtCore import QCoreApplication

        QCoreApplication.quit()

    @Property(list, notify=fansChanged)
    def fans(self):
        return self._fans

    # --------------------------------------------------------------- backdrop
    @Property(str, notify=stateChanged)
    def backdropUrl(self):
        if self._backdrop_rev == 0:
            return ""
        return f"image://backdrop/{self._backdrop_rev}"

    @Property(int, notify=stateChanged)
    def wpX(self):
        return self._wp_x

    @Property(int, notify=stateChanged)
    def wpY(self):
        return self._wp_y

    @Property(int, notify=stateChanged)
    def wpW(self):
        return self._wp_w

    @Property(int, notify=stateChanged)
    def wpH(self):
        return self._wp_h

    @Slot(int, int, int, int, float)
    def update_screen(self, x: int, y: int, w: int, h: int, dpr: float):
        self._wp_x, self._wp_y = x, y
        self._wp_w, self._wp_h = w, h
        self._dpr = dpr or 1.0
        self._wp_hwnd = 0
        self._wp_mirror = 0
        self._wp_retry = 0.0
        self._last_capture = 0.0
        self.refresh_backdrop()

    @Slot()
    def refresh_backdrop(self):
        """Capture what is really behind the widget and blur it for the glass."""
        if not self._window:
            return
        if not self._window.isVisible():
            # Hidden to the tray: nothing to paint, so do not capture either.
            return
        self._sync_geometry()
        w, h = self._window.width(), self._window.height()
        if w <= 0 or h <= 0:
            return
        now = time.monotonic()
        if self._backdrop_rev > 0 and now - self._last_capture < 0.04:
            return
        geom = (self._win_x, self._win_y, w, h)
        static = self._backdrop_source in ("wallpaper-file", "printwindow")
        if (
            static
            and self._backdrop_rev > 0
            and geom == self._static_geom
            and now - self._last_capture < STATIC_REFRESH
        ):
            return
        self._last_capture = now
        self._static_geom = geom

        if os.environ.get("LGS_WALLPAPER"):
            grabbed = self._backdrop_from_file(w, h)
            source = "preview-file"
        else:
            grabbed = self._grab_backdrop(w, h)
            source = self._backdrop_source
        if grabbed is None:
            if self._backdrop_logged != "failed":
                self._backdrop_logged = "failed"
                logutil.log("backdrop: no source available")
            return
        img, ox, oy = grabbed
        if img.isNull():
            return
        if source != self._backdrop_logged:
            self._backdrop_logged = source
            logutil.log(
                f"backdrop source: {source or 'none'} {img.width()}x{img.height()}"
            )

        # Blur a small copy: it is cheap enough to run every frame and the
        # linear upscale in QML hides the reduced resolution.
        ds = BACKDROP_DOWNSCALE
        small = img.scaled(
            max(1, img.width() // ds),
            max(1, img.height() // ds),
            Qt.IgnoreAspectRatio,
            Qt.SmoothTransformation,
        )
        radius = max(2, int(BLUR_RADIUS / ds))
        blurred = _blur(small, radius)
        cw = max(1, int(w * (img.devicePixelRatio() or 1.0) / ds))
        ch = max(1, int(h * (img.devicePixelRatio() or 1.0) / ds))
        out = blurred.copy(int(ox / ds), int(oy / ds), cw, ch)
        if out.isNull():
            return

        self._backdrop = out
        self._backdrop_rev += 1
        self.provider.image = self._backdrop
        self.stateChanged.emit()

    def _grab_backdrop(self, w: int, h: int):
        """Grab the desktop behind the widget, with graceful fallbacks.

        Order: Wallpaper Engine window (live) -> wallpaper file (static) ->
        PrintWindow of the desktop. The last one also covers wallpapers whose
        files are unavailable (slideshow, Spotlight, policies).
        """
        screen = self._window.screen()
        dpr = screen.devicePixelRatio() or 1.0
        pad = int(BACKDROP_PAD * dpr)
        wx = int(self._win_x * dpr)
        wy = int(self._win_y * dpr)
        ww = int(w * dpr)
        wh = int(h * dpr)

        def clamp_region(left, top, width, height):
            x0 = max(wx - pad, left)
            y0 = max(wy - pad, top)
            x1 = min(wx + ww + pad, left + width)
            y1 = min(wy + wh + pad, top + height)
            if x1 - x0 < 8 or y1 - y0 < 8:
                return None
            return x0, y0, x1 - x0, y1 - y0

        now = time.monotonic()
        cx, cy = wx + ww // 2, wy + wh // 2
        hwnd = self._wp_hwnd
        if hwnd and not winutil.is_window(hwnd):
            hwnd = 0
            self._wp_hwnd = 0
        if hwnd:
            r = winutil.window_rect(hwnd)
            if not (r[0] <= cx < r[0] + r[2] and r[1] <= cy < r[1] + r[3]):
                # The widget moved to another monitor; look for its surface.
                hwnd = 0
                self._wp_hwnd = 0
                self._wp_mirror = 0
        if not hwnd and now >= self._wp_retry:
            hwnd = winutil.find_live_wallpaper_window(cx, cy)
            if hwnd:
                self._wp_hwnd = hwnd
                logutil.log(f"wallpaper window: {hwnd}")
            else:
                self._wp_retry = now + 5.0
        if hwnd:
            rect = winutil.window_rect(hwnd)
            region = clamp_region(*rect)
            if region is not None:
                if self._wp_mirror and winutil.is_window(self._wp_mirror):
                    img = self._capture_mirror(self._wp_mirror, rect, region)
                    if img is not None:
                        self._backdrop_source = "wallpaper-engine"
                        return img, wx - region[0], wy - region[1]
                    self._wp_mirror = 0

                img = winutil.capture_window_region(hwnd, *region)
                if img is not None and not img.isNull() and not _is_black(img):
                    self._backdrop_source = "wallpaper-engine"
                    return img, wx - region[0], wy - region[1]

                # Secondary-monitor clone views are not readable via GDI, but
                # they mirror the primary surface: sample the same relative area.
                for other in winutil.all_live_wallpaper_windows():
                    if other == hwnd:
                        continue
                    img = self._capture_mirror(other, rect, region)
                    if img is not None:
                        self._wp_mirror = other
                        self._backdrop_source = "wallpaper-engine"
                        return img, wx - region[0], wy - region[1]

                logutil.log(
                    f"wpe capture failed region={region} pos=({self._win_x},{self._win_y})"
                )
            # Cached window stopped producing frames: look for a new one later.
            self._wp_hwnd = 0
            self._wp_mirror = 0
            self._wp_retry = now + 5.0

        got = self._backdrop_from_file(w, h)
        if got is not None and not _is_black(got[0]):
            self._backdrop_source = "wallpaper-file"
            return got

        geo = screen.geometry()
        region = clamp_region(
            int(geo.x() * dpr), int(geo.y() * dpr), int(geo.width() * dpr), int(geo.height() * dpr)
        )
        if region is not None:
            img = winutil.capture_desktop_region(*region)
            if img is not None and not img.isNull() and not _is_black(img):
                self._backdrop_source = "printwindow"
                return img, wx - region[0], wy - region[1]

        self._backdrop_source = ""
        return None

    def _capture_mirror(self, mirror: int, rect, region):
        """Read the same relative area from another monitor's wallpaper copy.

        Secondary-monitor Wallpaper Engine views are often not readable with
        GDI, but they mirror the primary surface, so the same part of the image
        can be sampled from the readable window.
        """
        mx, my, mw, mh = winutil.window_rect(mirror)
        if mw < 8 or mh < 8:
            return None
        rx = (region[0] - rect[0]) / max(1, rect[2])
        ry = (region[1] - rect[1]) / max(1, rect[3])
        rw = region[2] / max(1, rect[2])
        rh = region[3] / max(1, rect[3])
        img = winutil.capture_window_region(
            mirror,
            mx + int(rx * mw),
            my + int(ry * mh),
            max(8, int(rw * mw)),
            max(8, int(rh * mh)),
        )
        if img is None or img.isNull() or _is_black(img):
            return None
        return img

    def _wallpaper_image(self):
        """Decode and scale the wallpaper file once, then reuse it.

        Re-reading a 4K image 20 times a second was the main CPU cost on
        machines without a live wallpaper window.
        """
        from PySide6.QtGui import QGuiApplication

        path = winutil.wallpaper_path()
        if not path or not os.path.exists(path):
            return None, None
        try:
            mtime = os.path.getmtime(path)
        except OSError:
            return None, None

        screens = QGuiApplication.screens()
        vx = min(s.geometry().x() for s in screens)
        vy = min(s.geometry().y() for s in screens)
        vw = max(s.geometry().x() + s.geometry().width() for s in screens) - vx
        vh = max(s.geometry().y() + s.geometry().height() for s in screens) - vy
        dpr = self._window.screen().devicePixelRatio() or 1.0

        key = (path, mtime, vx, vy, vw, vh, dpr)
        if key == self._wall_key and self._wall_image is not None:
            return self._wall_image, (vx, vy, dpr)

        img = QImage(path)
        if img.isNull():
            return None, None
        pw, ph = max(1, int(vw * dpr)), max(1, int(vh * dpr))
        img = img.scaled(pw, ph, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
        img = img.copy((img.width() - pw) // 2, (img.height() - ph) // 2, pw, ph)
        self._wall_key = key
        self._wall_image = img
        return img, (vx, vy, dpr)

    def _backdrop_from_file(self, w: int, h: int):
        img, layout = self._wallpaper_image()
        if img is None:
            return None
        vx, vy, dpr = layout

        pad = int(BACKDROP_PAD * dpr)
        px = int((self._win_x - vx - BACKDROP_PAD) * dpr)
        py = int((self._win_y - vy - BACKDROP_PAD) * dpr)
        rw = int(w * dpr) + pad * 2
        rh = int(h * dpr) + pad * 2

        # Clamp to the wallpaper and remember how much padding was cut off.
        sx = max(0, min(px, img.width() - 1))
        sy = max(0, min(py, img.height() - 1))
        ex = max(sx + 1, min(px + rw, img.width()))
        ey = max(sy + 1, min(py + rh, img.height()))
        return img.copy(sx, sy, ex - sx, ey - sy), px + pad - sx, py + pad - sy

    # ------------------------------------------------------------------ theme
    @staticmethod
    def _detect_dark() -> bool:
        try:
            import winreg

            key = winreg.OpenKey(
                winreg.HKEY_CURRENT_USER,
                r"Software\Microsoft\Windows\CurrentVersion\Themes\Personalize",
            )
            value, _ = winreg.QueryValueEx(key, "AppsUseLightTheme")
            return int(value) == 0
        except Exception:
            return True

    def _refresh_theme(self):
        override = os.environ.get("LGS_THEME")
        if override in ("dark", "light"):
            dark = override == "dark"
        else:
            mode = self._cfg.get("theme", "auto")
            if mode == "auto":
                dark = self._detect_dark()
            else:
                dark = mode == "dark"
        if dark != self._dark:
            self._dark = dark
            self.stateChanged.emit()

    # --------------------------------------------------------------- settings
    @Slot(str)
    def set_fan(self, name: str):
        self._fan_name = name
        self._cfg["fan_sensor"] = name
        config.save(self._cfg)
        self._worker.set_fan(name)
        self.sampleTick.emit()

    @Slot(str)
    def set_theme(self, mode: str):
        self._cfg["theme"] = mode
        config.save(self._cfg)
        self._refresh_theme()
        self.stateChanged.emit()

    @Slot(int, int)
    def save_position(self, x: int, y: int):
        self._cfg["x"] = x
        self._cfg["y"] = y
        config.save(self._cfg)

    @Slot(result="QVariant")
    def saved_position(self):
        return {"x": self._cfg.get("x"), "y": self._cfg.get("y")}

    @Property(int, constant=True)
    def savedX(self):
        value = self._cfg.get("x")
        return -100000 if value is None else int(value)

    @Property(int, constant=True)
    def savedY(self):
        value = self._cfg.get("y")
        return -100000 if value is None else int(value)

    @Slot()
    def open_menu(self):
        self.menuRequested.emit()

    @Slot()
    def refresh_wallpaper(self):
        self.refresh_backdrop()

    def shutdown(self):
        self._worker.stop()
        self._cfg["x"] = self._window.x() if self._window else self._cfg.get("x")
        self._cfg["y"] = self._window.y() if self._window else self._cfg.get("y")
        config.save(self._cfg)






