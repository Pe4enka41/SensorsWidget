import QtQuick

Item {
    id: root
    width: 356
    height: 96

    property bool dark: backend.darkTheme
    property point mouse: Qt.point(-100, -100)
    property bool hovering: mouse.x >= 0 && mouse.x <= width && mouse.y >= 0 && mouse.y <= height

    readonly property color ink: dark ? "#F1F5F8" : "#12181C"
    readonly property color inkDim: dark ? "#9DB0BD" : "#5B6A74"
    readonly property color hairline: dark ? Qt.rgba(1, 1, 1, 0.08) : Qt.rgba(0, 0, 0, 0.09)

    function subLine(load, power) {
        var parts = []
        if (load >= 0) parts.push(Math.round(load) + " %")
        if (power >= 1) parts.push(Math.round(power) + " W")
        return parts.length ? parts.join("  ·  ") : "—"
    }

    Image {
        id: wallImage
        source: backend.backdropUrl
        width: root.width
        height: root.height
        cache: false
        asynchronous: false
    }

    ShaderEffect {
        id: glass
        anchors.fill: parent

        property var srcTex: ShaderEffectSource {
            sourceItem: wallImage
            sourceRect: Qt.rect(0, 0, Math.max(1, root.width), Math.max(1, root.height))
            live: true
            hideSource: true
            recursive: false
        }

        property vector2d tileSize: Qt.vector2d(root.width, root.height)
        property real radius: 16
        property real lens: 14
        property real hover: root.hovering ? 1.0 : 0.0
        property vector2d lightPos: Qt.vector2d(mouse.x / root.width, mouse.y / root.height)
        property real darkMode: root.dark ? 1.0 : 0.0

        fragmentShader: "glass.frag.qsb"
    }

    Rectangle {
        x: 123
        y: 20
        width: 1
        height: 56
        color: root.hairline
    }

    Rectangle {
        x: 233
        y: 20
        width: 1
        height: 56
        color: root.hairline
    }

    // CPU ---------------------------------------------------------------------
    Text {
        x: 18; y: 12
        text: "CPU"
        color: root.inkDim
        font.family: "Segoe UI Variable Display"
        font.pixelSize: 9
        font.weight: Font.DemiBold
        font.letterSpacing: 1.3
    }

    Text {
        id: cpuValue
        x: 18; y: 22
        text: backend.cpuTemp < 0 ? "—" : Math.round(backend.cpuTemp).toString()
        color: backend.cpuTemp < 0 ? Qt.rgba(root.inkDim.r, root.inkDim.g, root.inkDim.b, 0.5) : root.ink
        font.family: "Segoe UI Variable Display"
        font.pixelSize: 30
        font.weight: Font.Light
        font.letterSpacing: -0.5
    }

    Text {
        x: cpuValue.x + cpuValue.implicitWidth + 3
        y: cpuValue.y + cpuValue.height - 16
        text: backend.cpuTemp < 0 ? "" : "°C"
        color: root.inkDim
        font.family: "Segoe UI Variable Display"
        font.pixelSize: 11
        font.weight: Font.Medium
    }

    Text {
        x: 18; y: 66
        text: root.subLine(backend.cpuLoad, backend.cpuPower)
        color: root.inkDim
        opacity: 0.72
        font.family: "Segoe UI Variable Display"
        font.pixelSize: 9
        font.weight: Font.Normal
    }

    // Кулер -------------------------------------------------------------------
    Text {
        x: 128; y: 12
        text: "КУЛЕР"
        color: root.inkDim
        font.family: "Segoe UI Variable Display"
        font.pixelSize: 9
        font.weight: Font.DemiBold
        font.letterSpacing: 1.3
    }

    Text {
        id: fanValue
        x: 128; y: 22
        text: backend.fanPct < 0 ? "—" : Math.round(backend.fanPct).toString()
        color: backend.fanPct < 0 ? Qt.rgba(root.inkDim.r, root.inkDim.g, root.inkDim.b, 0.5) : root.ink
        font.family: "Segoe UI Variable Display"
        font.pixelSize: 30
        font.weight: Font.Light
        font.letterSpacing: -0.5
    }

    Text {
        x: fanValue.x + fanValue.implicitWidth + 3
        y: fanValue.y + fanValue.height - 16
        text: backend.fanPct < 0 ? "" : "%"
        color: root.inkDim
        font.family: "Segoe UI Variable Display"
        font.pixelSize: 11
        font.weight: Font.Medium
    }

    Text {
        x: 128; y: 66
        text: backend.fanRpm < 0 ? "—" : Math.round(backend.fanRpm) + " rpm"
        color: root.inkDim
        opacity: 0.72
        font.family: "Segoe UI Variable Display"
        font.pixelSize: 9
        font.weight: Font.Normal
    }

    // GPU ---------------------------------------------------------------------
    Text {
        x: 238; y: 12
        text: "GPU"
        color: root.inkDim
        font.family: "Segoe UI Variable Display"
        font.pixelSize: 9
        font.weight: Font.DemiBold
        font.letterSpacing: 1.3
    }

    Text {
        id: gpuValue
        x: 238; y: 22
        text: backend.gpuTemp < 0 ? "—" : Math.round(backend.gpuTemp).toString()
        color: backend.gpuTemp < 0 ? Qt.rgba(root.inkDim.r, root.inkDim.g, root.inkDim.b, 0.5) : root.ink
        font.family: "Segoe UI Variable Display"
        font.pixelSize: 30
        font.weight: Font.Light
        font.letterSpacing: -0.5
    }

    Text {
        x: gpuValue.x + gpuValue.implicitWidth + 3
        y: gpuValue.y + gpuValue.height - 16
        text: backend.gpuTemp < 0 ? "" : "°C"
        color: root.inkDim
        font.family: "Segoe UI Variable Display"
        font.pixelSize: 11
        font.weight: Font.Medium
    }

    Text {
        x: 238; y: 66
        text: root.subLine(backend.gpuLoad, backend.gpuPower)
        color: root.inkDim
        opacity: 0.72
        font.family: "Segoe UI Variable Display"
        font.pixelSize: 9
        font.weight: Font.Normal
    }

    MouseArea {
        id: dragArea
        anchors.fill: parent
        hoverEnabled: true
        acceptedButtons: Qt.LeftButton | Qt.RightButton
        cursorShape: backend.locked
            ? Qt.ArrowCursor
            : (pressed ? Qt.ClosedHandCursor : Qt.OpenHandCursor)

        onPressed: function (mouse) {
            if (mouse.button === Qt.RightButton) {
                backend.open_menu()
                return
            }
            if (!backend.locked) backend.drag_begin()
        }

        onPositionChanged: function (mouse) {
            root.mouse = Qt.point(mouse.x, mouse.y)
            if (pressed && !backend.locked) backend.drag_update()
        }

        onReleased: {
            if (!backend.locked) backend.drag_end()
        }
        onExited: root.mouse = Qt.point(-100, -100)
        onDoubleClicked: backend.open_menu()
    }
}


