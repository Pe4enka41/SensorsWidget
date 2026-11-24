"""Liquid glass sensor widget: entry point, tray, single instance."""
from __future__ import annotations

import os
import sys
import time

from PySide6.QtCore import QPoint, Qt, QTimer, QUrl
from PySide6.QtGui import QColor, QGuiApplication, QIcon
from PySide6.QtQuick import QQuickView, QQuickWindow

import config
import logutil
import winutil
from backend import Backend
from tray import TrayHost

ROOT = os.path.dirname(os.path.abspath(__file__))


def main() -> int:
    app = QGuiApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName("LiquidGlassSensors")

    logutil.trim()
    logutil.log(
        f"start v{config.VERSION} admin={winutil.is_admin()} python={sys.version.split()[0]}"
    )

    icon_path = os.path.join(ROOT, "assets", "icon.ico")
    if os.path.exists(icon_path):
        app.setWindowIcon(QIcon(icon_path))

    host = TrayHost(os.environ.get("LGS_INSTANCE", "LiquidGlassSensorsHost"))
    if host.already_running:
        host.notify_existing()
        return 0

    backend = Backend()
    QQuickWindow.setDefaultAlphaBuffer(True)

    view = QQuickView()
    view.engine().addImageProvider("backdrop", backend.provider)
    view.setColor(QColor(0, 0, 0, 0))
    view.setFlags(Qt.FramelessWindowHint | Qt.Tool | Qt.NoDropShadowWindowHint)
    view.setResizeMode(QQuickView.SizeViewToRootObject)
    view.rootContext().setContextProperty("backend", backend)
    print("stage: loading qml", flush=True)
    view.setSource(QUrl.fromLocalFile(os.path.join(ROOT, "qml", "Main.qml")))
    print("stage: qml loaded", flush=True)
    if view.status() == QQuickView.Error:
        for err in view.errors():
            print("QML error:", err.toString())
            logutil.log("QML error: " + err.toString())
        return 1
    window = view
    backend.attach_window(window)
    print("stage: window attached", flush=True)

    menu = QQuickView()
    menu.setColor(QColor(0, 0, 0, 0))
    menu.setFlags(
        Qt.FramelessWindowHint | Qt.Tool | Qt.WindowStaysOnTopHint | Qt.NoDropShadowWindowHint
    )
    menu.setResizeMode(QQuickView.SizeViewToRootObject)
    menu.rootContext().setContextProperty("backend", backend)
    menu.setSource(QUrl.fromLocalFile(os.path.join(ROOT, "qml", "Menu.qml")))
    if menu.status() == QQuickView.Error:
        for err in menu.errors():
            print("Menu error:", err.toString())
            logutil.log("Menu error: " + err.toString())
        return 1

    view.show()

    sx, sy = backend.savedX, backend.savedY
    if sx > -99999 and sy > -99999:
        view.setPosition(sx, sy)
    else:
        geo = QGuiApplication.primaryScreen().availableGeometry()
        view.setPosition(
            geo.x() + geo.width() - view.width() - 56,
            geo.y() + geo.height() - view.height() - 96,
        )
    backend.enable_no_activate()
    backend.refresh_backdrop()
    view.activeChanged.connect(lambda: backend.set_active(view.isActive()))

    def on_screen_changed(screen):
        try:
            geo = screen.geometry()
            backend.update_screen(
                geo.x(), geo.y(), geo.width(), geo.height(), screen.devicePixelRatio()
            )
        except Exception:
            pass

    view.screenChanged.connect(on_screen_changed)

    # ------------------------------------------------------------------ menu
    menu_watch = QTimer()
    menu_watch.setInterval(35)
    menu_armed = False
    menu_prev_down = False
    menu_hidden_at = 0.0

    def hide_menu():
        nonlocal menu_hidden_at
        menu_watch.stop()
        menu_hidden_at = time.monotonic()
        if menu.isVisible():
            menu.hide()

    def open_menu(x: int | None = None, y: int | None = None):
        nonlocal menu_armed, menu_prev_down
        if x is None or y is None:
            # The click that closed the menu must not fall through to the
            # widget and open it again.
            if time.monotonic() - menu_hidden_at < 0.2:
                return
            x, y = winutil.cursor_pos()
        root = menu.rootObject()
        mw = int(root.width()) if root is not None else 246
        mh = int(root.height()) if root is not None else 300
        screen = QGuiApplication.screenAt(QPoint(x, y)) or QGuiApplication.primaryScreen()
        geo = screen.availableGeometry()
        x = min(max(x, geo.left() + 4), geo.right() - mw - 4)
        y = min(max(y, geo.top() + 4), geo.bottom() - mh - 4)
        menu.setPosition(x, y)
        menu.show()
        menu_armed = False
        menu_prev_down = True
        menu_watch.start()

    def poll_menu():
        nonlocal menu_armed, menu_prev_down
        if not menu.isVisible():
            menu_watch.stop()
            return
        root = menu.rootObject()
        if root is not None:
            mh = int(root.height())
            screen = QGuiApplication.screenAt(
                QPoint(menu.x(), menu.y())
            ) or QGuiApplication.primaryScreen()
            bottom = screen.availableGeometry().bottom() - mh - 4
            if menu.y() > bottom:
                menu.setY(bottom)
        down = winutil.mouse_buttons_down()
        if not menu_armed:
            if not down:
                menu_armed = True
            menu_prev_down = down
            return
        if down and not menu_prev_down:
            x, y = winutil.cursor_pos()
            if not menu.geometry().contains(x, y):
                hide_menu()
                return
        menu_prev_down = down

    menu_watch.timeout.connect(poll_menu)
    backend.menuRequested.connect(lambda: open_menu())
    backend.menuDismissed.connect(hide_menu)

    # ------------------------------------------------------------- window ops
    def toggle_visible():
        hide_menu()
        window.setVisible(not window.isVisible())

    def show_widget():
        hide_menu()
        window.setVisible(True)

    def reset_position():
        geo = window.screen().geometry()
        window.setX(geo.x() + geo.width() - window.width() - 56)
        window.setY(geo.y() + geo.height() - window.height() - 96)
        backend.save_position(window.x(), window.y())

    backend.visibilityRequested.connect(toggle_visible)
    backend.resetRequested.connect(reset_position)

    # ------------------------------------------------------------------ tray
    host.on_show = show_widget
    host.on_left = toggle_visible
    host.on_right = lambda: open_menu()
    host.add_icon(icon_path, "Температура CPU, кулер, GPU")

    # --------------------------------------------------------------- shutdown
    def shutdown():
        backend.shutdown()
        host.remove_icon()
        # Destroy the QML scenes while the Python bridge is still alive so the
        # bindings don't evaluate against a freed object during teardown.
        menu.setSource(QUrl())
        view.setSource(QUrl())

    app.aboutToQuit.connect(shutdown)
    backend.start()

    # ------------------------------------------------------------- dev shots
    shot_path = os.environ.get("LGS_SHOT")
    if shot_path:
        def capture():
            image = window.grabWindow()
            image.save(shot_path)
            print("shot saved", shot_path, image.size(), flush=True)
            if os.environ.get("LGS_QUIT"):
                app.quit()

        QTimer.singleShot(int(os.environ.get("LGS_SHOT_DELAY", "4500")), capture)

    menu_shot = os.environ.get("LGS_MENU_SHOT")
    if menu_shot:
        def capture_menu():
            open_menu(400, 260)
            if os.environ.get("LGS_MENU_FANS"):
                root = menu.rootObject()
                if root is not None:
                    root.setProperty("fansOpen", True)
            QTimer.singleShot(500, finish_menu_shot)

        def finish_menu_shot():
            image = menu.grabWindow()
            image.save(menu_shot)
            print("menu shot saved", menu_shot, image.size(), flush=True)
            app.quit()

        QTimer.singleShot(int(os.environ.get("LGS_SHOT_DELAY", "3500")), capture_menu)

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
