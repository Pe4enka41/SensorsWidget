import QtQuick

Item {
    id: item

    property string text: ""
    property string hint: ""
    property bool checked: false
    property bool arrow: false
    property bool danger: false
    property bool separator: false
    property bool active: true
    property bool indent: false

    signal clicked()

    width: parent ? parent.width : 0
    height: separator ? 9 : 30

    Rectangle {
        visible: item.separator
        anchors.verticalCenter: parent.verticalCenter
        width: parent.width
        height: 1
        color: Qt.rgba(1, 1, 1, 0.08)
    }

    Rectangle {
        anchors.fill: parent
        visible: !item.separator
        radius: 6
        color: mouse.containsMouse && item.active ? Qt.rgba(1, 1, 1, 0.07) : "transparent"
    }

    Text {
        visible: !item.separator
        anchors.left: parent.left
        anchors.leftMargin: item.indent ? 20 : 10
        anchors.verticalCenter: parent.verticalCenter
        anchors.right: hintText.visible ? hintText.left : parent.right
        anchors.rightMargin: 8
        elide: Text.ElideRight
        text: item.text
        color: !item.active ? "#71808B" : (item.danger ? "#E39191" : "#E9EEF3")
        font.family: "Segoe UI Variable Display"
        font.pixelSize: 12
    }

    Text {
        id: hintText
        visible: item.hint !== "" && !item.separator
        anchors.right: checkText.visible ? checkText.left : parent.right
        anchors.rightMargin: checkText.visible ? 6 : 10
        anchors.verticalCenter: parent.verticalCenter
        text: item.hint
        color: "#8D9CA8"
        font.family: "Segoe UI Variable Display"
        font.pixelSize: 10
    }

    Text {
        id: checkText
        visible: item.checked && !item.separator
        anchors.right: parent.right
        anchors.rightMargin: 10
        anchors.verticalCenter: parent.verticalCenter
        text: "✓"
        color: "#9CC6DA"
        font.pixelSize: 11
    }

    Text {
        visible: item.arrow && !item.separator
        anchors.right: parent.right
        anchors.rightMargin: 8
        anchors.verticalCenter: parent.verticalCenter
        anchors.verticalCenterOffset: -1
        text: "›"
        color: "#8D9CA8"
        font.pixelSize: 16
    }

    MouseArea {
        id: mouse
        anchors.fill: parent
        hoverEnabled: true
        enabled: !item.separator && item.active
        onClicked: item.clicked()
    }
}
