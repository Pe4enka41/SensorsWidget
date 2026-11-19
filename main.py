"""Liquid glass sensor widget: entry point, tray, single instance."""
from __future__ import annotations

import os
import sys

from PySide6.QtCore import QPoint, Qt, QTimer, QUrl
from PySide6.QtGui import (
    QAction,
    QActionGroup,
    QColor,
    QGuiApplication,
    QIcon,
    QLinearGradient,
    QPainter,
    QPen,
    QPixmap,
)
from PySide6.QtNetwork import QLocalServer, QLocalSocket
from PySide6.QtQuick import QQuickView, QQuickWindow
from PySide6.QtWidgets import QApplication, QMenu, QSystemTrayIcon

import config
import winutil
from backend import Backend

ROOT = os.path.dirname(os.path.abspath(__file__))
SINGLE_NAME = "LiquidGlassSensors-widget"


def make_icon() -> QIcon:
    pm = QPixmap(64, 64)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)
    grad = QLinearGradient(0, 0, 64, 64)
    grad.setColorAt(0.0, QColor(72, 92, 106, 255))
    grad.setColorAt(1.0, QColor(24, 31, 38, 255))
    p.setBrush(grad)
    p.setPen(QPen(QColor(255, 255, 255, 60), 2))
    p.drawRoundedRect(6, 6, 52, 52, 16, 16)
    bars = [(18, 30, "#9CD3E6"), (30, 42, "#A9D3BE"), (42, 24, "#E4C79A")]
    for x, top, color in bars:
        p.setPen(Qt.NoPen)
        p.setBrush(QColor(color))
        p.drawRoundedRect(x, top, 5, 46 - top, 2.5, 2.5)
    p.end()
    return QIcon(pm)


def main() -> int:
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName("LiquidGlassSensors")
    app.setWindowIcon(make_icon())

    socket = QLocalSocket()
    socket.connectToServer(SINGLE_NAME)
    if socket.waitForConnected(250):
        socket.write(b"show")
        socket.flush()
        socket.waitForBytesWritten(250)
        return 0

    server = QLocalServer()
    QLocalServer.removeServer(SINGLE_NAME)
    server.listen(SINGLE_NAME)

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
        return 1
    window = view
    backend.attach_window(window)
    print("stage: window attached", flush=True)

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

    menu = QMenu()
    toggle_action = menu.addAction("Скрыть виджет")
    menu.addSeparator()

    fan_menu = menu.addMenu("Датчик кулера")
    theme_menu = menu.addMenu("Тема")
    theme_group = QActionGroup(menu)
    theme_group.setExclusive(True)
    theme_actions = {}
    for key, label in (("auto", "Как в системе"), ("dark", "Тёмная"), ("light", "Светлая")):
        action = QAction(label, menu)
        action.setCheckable(True)
        action.setChecked(backend._cfg.get("theme", "auto") == key)
        action.triggered.connect(lambda _checked, k=key: backend.set_theme(k))
        theme_group.addAction(action)
        theme_menu.addAction(action)
        theme_actions[key] = action

    autostart_action = menu.addAction("Запускать с Windows")
    autostart_action.setCheckable(True)
    autostart_action.setChecked(config.autostart_enabled())
    lock_action = menu.addAction("Зафиксировать положение")
    lock_action.setCheckable(True)
    lock_action.setChecked(backend.locked)
    menu.addSeparator()
    if not winutil.is_admin():
        elevate_action = menu.addAction("Перезапустить с правами администратора")
        elevate_action.triggered.connect(config.restart_elevated)
    reset_action = menu.addAction("Сбросить позицию")
    menu.addSeparator()
    quit_action = menu.addAction("Выход")

    def rebuild_fans():
        fan_menu.clear()
        group = QActionGroup(fan_menu)
        group.setExclusive(True)
        current = backend._fan_name
        for fan in backend.fans:
            label = fan.get("name") or "?"
            if fan.get("rpm"):
                label += f"  ·  {int(fan['rpm'])} rpm"
            action = QAction(label, fan_menu)
            action.setCheckable(True)
            action.setChecked(fan.get("name") == current)
            action.triggered.connect(lambda _c, n=fan.get("name"): backend.set_fan(n))
            group.addAction(action)
            fan_menu.addAction(action)
        if not backend.fans:
            fan_menu.addAction("Нет данных").setEnabled(False)

    rebuild_fans()
    backend.fansChanged.connect(rebuild_fans)

    def toggle_visible():
        window.setVisible(not window.isVisible())
        toggle_action.setText("Скрыть виджет" if window.isVisible() else "Показать виджет")

    def reset_position():
        screen = window.screen()
        geo = screen.geometry()
        window.setX(geo.x() + geo.width() - window.width() - 56)
        window.setY(geo.y() + geo.height() - window.height() - 96)
        backend.save_position(window.x(), window.y())

    def toggle_autostart(checked: bool):
        config.set_autostart(checked)
        backend._cfg["autostart"] = checked
        config.save(backend._cfg)
        QTimer.singleShot(2000, lambda: autostart_action.setChecked(config.autostart_enabled()))

    toggle_action.triggered.connect(toggle_visible)
    reset_action.triggered.connect(reset_position)
    autostart_action.toggled.connect(toggle_autostart)
    lock_action.toggled.connect(backend.set_locked)
    quit_action.triggered.connect(app.quit)

    menu_watch = QTimer()
    menu_watch.setInterval(35)
    menu_armed = False
    menu_prev_down = False

    def poll_menu():
        nonlocal menu_armed, menu_prev_down
        if not menu.isVisible():
            menu_watch.stop()
            return
        down = winutil.mouse_buttons_down()
        if not menu_armed:
            if not down:
                menu_armed = True
            menu_prev_down = down
            return
        if down and not menu_prev_down:
            x, y = winutil.cursor_pos()
            if not menu.geometry().contains(x, y):
                menu.close()
                menu_watch.stop()
        menu_prev_down = down

    menu_watch.timeout.connect(poll_menu)

    def show_menu():
        nonlocal menu_armed, menu_prev_down
        menu.popup(QPoint(*winutil.cursor_pos()))
        menu_armed = False
        menu_prev_down = True
        menu_watch.start()

    backend.menuRequested.connect(show_menu)

    tray = QSystemTrayIcon(make_icon(), app)
    tray.setToolTip("Температура CPU, кулер, GPU")
    tray.setContextMenu(menu)
    tray.activated.connect(
        lambda reason: show_menu() if reason == QSystemTrayIcon.Trigger else None
    )
    tray.show()

    def on_new_connection():
        conn = server.nextPendingConnection()
        if conn:
            conn.readyRead.connect(lambda: window.setVisible(True))
            conn.disconnected.connect(conn.deleteLater)

    server.newConnection.connect(on_new_connection)

    def shutdown():
        backend.shutdown()
        tray.hide()
        # Destroy the QML scene while the Python bridge is still alive so the
        # bindings don't evaluate against a freed object during teardown.
        view.setSource(QUrl())

    app.aboutToQuit.connect(shutdown)
    backend.start()

    shot_path = os.environ.get("LGS_SHOT")
    if shot_path:
        def capture():
            image = window.grabWindow()
            image.save(shot_path)
            print("shot saved", shot_path, image.size(), flush=True)
            if os.environ.get("LGS_QUIT"):
                app.quit()

        QTimer.singleShot(int(os.environ.get("LGS_SHOT_DELAY", "4500")), capture)

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
