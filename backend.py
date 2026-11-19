"""Qt bridge: sensor data, wallpaper glass texture, window behaviour, tray state."""
from __future__ import annotations

import os
import time
from collections import deque

from PySide6.QtCore import Property, QObject, QRectF, Qt, QTimer, Signal, Slot
from PySide6.QtGui import QImage, QPainter, QPixmap
from PySide6.QtQuick import QQuickImageProvider
from PySide6.QtWidgets import QGraphicsBlurEffect, QGraphicsPixmapItem, QGraphicsScene

import config
import winutil
from sensors import SensorWorker

ROOT = os.path.dirname(os.path.abspath(__file__))
HISTORY = 48
BACKDROP_PAD = 80
BLUR_RADIUS = 26.0
BACKDROP_DOWNSCALE = 3


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
    margin = radius * 2
    scene = QGraphicsScene()
    item = QGraphicsPixmapItem(QPixmap.fromImage(img))
    effect = QGraphicsBlurEffect()
    effect.setBlurRadius(radius)
    effect.setBlurHints(QGraphicsBlurEffect.QualityHint)
    item.setGraphicsEffect(effect)
    scene.addItem(item)
    out = QImage(
        img.width() + margin * 2, img.height() + margin * 2, QImage.Format_ARGB32_Premultiplied
    )
    out.fill(0)
    painter = QPainter(out)
    target = QRectF(0, 0, out.width(), out.height())
    scene.render(painter, target, target.translated(-margin, -margin))
    painter.end()
    return out.copy(margin, margin, img.width(), img.height())


class Backend(QObject):
    sampleTick = Signal()
    fansChanged = Signal()
    stateChanged = Signal()
    menuRequested = Signal()
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
        self._last_capture = 0.0
        self._locked = bool(self._cfg.get("locked", False))
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
        self.refresh_backdrop()

    @Slot()
    def refresh_backdrop(self):
        """Capture what is really behind the widget and blur it for the glass."""
        if not self._window:
            return
        self._sync_geometry()
        w, h = self._window.width(), self._window.height()
        if w <= 0 or h <= 0:
            return
        now = time.monotonic()
        if self._backdrop_rev > 0 and now - self._last_capture < 0.04:
            return
        self._last_capture = now

        if os.environ.get("LGS_WALLPAPER"):
            grabbed = self._backdrop_from_file(w, h)
        else:
            grabbed = self._backdrop_from_screen(w, h)
        if grabbed is None:
            return
        img, ox, oy = grabbed
        if img.isNull():
            return

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

    def _backdrop_from_screen(self, w: int, h: int):
        """Grab the desktop right behind the widget.

        Wallpaper Engine paints into its own window, so reading that window's
        DC yields the live wallpaper without capturing the widget itself.
        Plain Windows wallpapers fall back to the wallpaper file.
        """
        screen = self._window.screen()
        hwnd = winutil.find_live_wallpaper_window(
            self._win_x + w // 2, self._win_y + h // 2
        )
        if not hwnd:
            return self._backdrop_from_file(w, h)

        dpr = screen.devicePixelRatio() or 1.0
        img = winutil.capture_window_region(
            hwnd,
            int((self._win_x - BACKDROP_PAD) * dpr),
            int((self._win_y - BACKDROP_PAD) * dpr),
            int((w + BACKDROP_PAD * 2) * dpr),
            int((h + BACKDROP_PAD * 2) * dpr),
        )
        if img is None or img.isNull():
            return self._backdrop_from_file(w, h)
        pad = int(BACKDROP_PAD * dpr)
        return img, pad, pad

    def _backdrop_from_file(self, w: int, h: int):
        path = winutil.wallpaper_path()
        if not path or not os.path.exists(path):
            return None
        img = QImage(path)
        if img.isNull():
            return None

        from PySide6.QtGui import QGuiApplication

        screen = self._window.screen()
        dpr = screen.devicePixelRatio() or 1.0
        vx = min(s.geometry().x() for s in QGuiApplication.screens())
        vy = min(s.geometry().y() for s in QGuiApplication.screens())
        vw = max(s.geometry().x() + s.geometry().width() for s in QGuiApplication.screens()) - vx
        vh = max(s.geometry().y() + s.geometry().height() for s in QGuiApplication.screens()) - vy
        pw, ph = max(1, int(vw * dpr)), max(1, int(vh * dpr))
        img = img.scaled(pw, ph, Qt.KeepAspectRatioByExpanding, Qt.SmoothTransformation)
        img = img.copy((img.width() - pw) // 2, (img.height() - ph) // 2, pw, ph)

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






