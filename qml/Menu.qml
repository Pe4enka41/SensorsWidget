import QtQuick

Item {
    id: root

    width: 246
    height: bg.height

    property bool fansOpen: false
    property bool themeOpen: false

    function run(action) {
        action()
        backend.dismiss_menu()
    }

    Rectangle {
        id: bg
        width: root.width
        height: column.height + 12
        radius: 10
        color: "#F2181D22"
        border.width: 1
        border.color: Qt.rgba(1, 1, 1, 0.10)

        Column {
            id: column
            x: 6
            y: 6
            width: parent.width - 12
            spacing: 0

            MenuItem {
                text: "Скрыть виджет"
                onClicked: root.run(function () { backend.request_toggle_visibility() })
            }

            MenuItem { separator: true }

            MenuItem {
                text: "Датчик кулера"
                arrow: true
                onClicked: {
                    root.fansOpen = !root.fansOpen
                    root.themeOpen = false
                }
            }

            Repeater {
                model: root.fansOpen ? backend.fans : []

                delegate: MenuItem {
                    indent: true
                    text: modelData.name
                    hint: modelData.rpm ? Math.round(modelData.rpm) + " rpm" : ""
                    checked: backend.fanName === modelData.name
                    onClicked: root.run(function () { backend.set_fan(modelData.name) })
                }
            }

            MenuItem {
                text: "Тема"
                arrow: true
                onClicked: {
                    root.themeOpen = !root.themeOpen
                    root.fansOpen = false
                }
            }

            Repeater {
                model: root.themeOpen
                    ? [
                        { key: "auto", label: "Как в системе" },
                        { key: "dark", label: "Тёмная" },
                        { key: "light", label: "Светлая" }
                    ]
                    : []

                delegate: MenuItem {
                    indent: true
                    text: modelData.label
                    checked: backend.themeMode === modelData.key
                    onClicked: root.run(function () { backend.set_theme(modelData.key) })
                }
            }

            MenuItem { separator: true }

            MenuItem {
                text: "Зафиксировать положение"
                checked: backend.locked
                onClicked: root.run(function () { backend.set_locked(!backend.locked) })
            }

            MenuItem {
                text: "Запускать с Windows"
                checked: backend.autostart
                onClicked: root.run(function () { backend.set_autostart(!backend.autostart) })
            }

            MenuItem { separator: true }

            MenuItem {
                visible: !backend.isAdmin
                text: "Перезапустить от администратора"
                onClicked: root.run(function () { backend.restart_elevated_app() })
            }

            MenuItem {
                text: "Сбросить позицию"
                onClicked: root.run(function () { backend.request_reset_position() })
            }

            MenuItem { separator: true }

            MenuItem {
                text: "Выход"
                danger: true
                onClicked: root.run(function () { backend.quit_app() })
            }
        }
    }
}
