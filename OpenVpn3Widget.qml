pragma ComponentBehavior: Bound

import QtQuick
import QtQuick.Layouts
import qs.Common
import qs.Modules.Plugins
import qs.Modals.FileBrowser
import qs.Widgets
import "."

PluginComponent {
    id: root

    layerNamespacePlugin: "openvpn3"
    popoutWidth: 420
    popoutHeight: Math.min(560, 150 + OpenVpn3Service.profiles.length * 88 + OpenVpn3Service.sessions.length * 52)

    readonly property bool hasFailedSession: OpenVpn3Service.sessions.some(session => session.state === "failed")
    readonly property color barStatusColor: {
        if ((!OpenVpn3Service.available && OpenVpn3Service.lastError.length > 0) || root.hasFailedSession)
            return Theme.error;
        if (OpenVpn3Service.refreshInFlight || OpenVpn3Service.hasTransitionalSession || OpenVpn3Service.sessions.some(session => session.state === "paused"))
            return Theme.warning;
        if (OpenVpn3Service.isConnected)
            return Theme.primary;
        return Theme.surfaceVariantText;
    }

    function stateLabel(state) {
        switch (state) {
        case "auth_required": return "Authentication required";
        case "connected": return "Connected";
        case "connecting": return "Connecting";
        case "disconnecting": return "Disconnecting";
        case "disconnected": return "Disconnected";
        case "failed": return "Connection failed";
        case "paused": return "Paused";
        case "preparing": return "Preparing";
        default: return "Unknown state";
        }
    }

    function stateIcon(state) {
        switch (state) {
        case "connected": return "vpn_lock";
        case "auth_required": return "key";
        case "paused": return "pause_circle";
        case "disconnecting": return "link_off";
        case "failed": return "error";
        case "disconnected": return "vpn_key_off";
        default: return "sync";
        }
    }

    function stateColor(state) {
        if (state === "connected")
            return Theme.primary;
        if (state === "failed")
            return Theme.error;
        if (["auth_required", "connecting", "disconnecting", "paused", "preparing"].includes(state))
            return Theme.warning;
        return Theme.surfaceVariantText;
    }

    horizontalBarPill: Component {
        Row {
            spacing: Theme.spacingXS

            Item {
                width: root.iconSize
                height: root.iconSize
                anchors.verticalCenter: parent.verticalCenter

                DankIcon {
                    anchors.centerIn: parent
                    name: "vpn_lock"
                    size: root.iconSize
                    color: OpenVpn3Service.isConnected ? Theme.primary : Theme.widgetIconColor
                }

                Rectangle {
                    width: 6
                    height: 6
                    radius: 3
                    color: root.barStatusColor
                    anchors.right: parent.right
                    anchors.bottom: parent.bottom
                }
            }

            StyledText {
                visible: OpenVpn3Service.connectedSessions.length > 0
                width: Math.min(implicitWidth, 160)
                text: {
                    const sessions = OpenVpn3Service.connectedSessions;
                    if (sessions.length === 1)
                        return sessions[0].config_name || "1 active";
                    return `${sessions.length} active`;
                }
                font.pixelSize: Theme.barTextSize(root.barThickness, root.barConfig?.fontScale)
                color: Theme.widgetTextColor
                elide: Text.ElideRight
                anchors.verticalCenter: parent.verticalCenter
            }
        }
    }

    verticalBarPill: Component {
        Column {
            spacing: 1

            Item {
                width: root.iconSize
                height: root.iconSize
                anchors.horizontalCenter: parent.horizontalCenter

                DankIcon {
                    anchors.centerIn: parent
                    name: "vpn_lock"
                    size: root.iconSize
                    color: OpenVpn3Service.isConnected ? Theme.primary : Theme.widgetIconColor
                }

                Rectangle {
                    width: 6
                    height: 6
                    radius: 3
                    color: root.barStatusColor
                    anchors.right: parent.right
                    anchors.bottom: parent.bottom
                }
            }

            StyledText {
                visible: OpenVpn3Service.connectedSessions.length > 0
                text: OpenVpn3Service.connectedSessions.length.toString()
                font.pixelSize: Theme.fontSizeSmall
                color: Theme.widgetTextColor
                anchors.horizontalCenter: parent.horizontalCenter
            }
        }
    }

    popoutContent: Component {
        PopoutComponent {
            id: popout
            property string pendingRemovePath: ""

            headerText: "Profiles"
            detailsText: {
                if (!OpenVpn3Service.available)
                    return OpenVpn3Service.lastError || "The service is unavailable";
                const count = OpenVpn3Service.connectedSessions.length;
                return count === 0 ? "No active sessions" : (count === 1 ? "1 active session" : `${count} active sessions`);
            }
            showCloseButton: true

            FileBrowserSurfaceModal {
                id: profileBrowser
                browserTitle: "Import profile"
                browserIcon: "vpn_key"
                browserType: "vpn"
                fileExtensions: ["*.ovpn", "*.conf"]
                parentPopout: popout.parentPopout

                onFileSelected: path => {
                    close();
                    OpenVpn3Service.importProfile(path.replace("file://", ""));
                }
            }

            headerActions: Component {
                Row {
                    spacing: Theme.spacingXS

                    DankActionButton {
                        iconName: "add"
                        iconColor: Theme.primary
                        buttonSize: 28
                        enabled: OpenVpn3Service.available && !OpenVpn3Service.actionInFlight
                        tooltipText: "Import profile"
                        tooltipSide: "bottom"
                        onClicked: profileBrowser.open()
                    }

                    DankActionButton {
                        iconName: "refresh"
                        iconColor: Theme.surfaceVariantText
                        buttonSize: 28
                        enabled: !OpenVpn3Service.refreshInFlight
                        tooltipText: "Refresh"
                        tooltipSide: "bottom"
                        onClicked: OpenVpn3Service.refresh()
                    }
                }
            }

            DankFlickable {
                width: parent.width
                height: Math.min(contentColumn.implicitHeight, 460)
                contentHeight: contentColumn.implicitHeight
                clip: true

                Column {
                    id: contentColumn
                    width: parent.width
                    spacing: Theme.spacingS

                StyledRect {
                    visible: OpenVpn3Service.lastError.length > 0
                    width: parent.width
                    height: errorText.implicitHeight + Theme.spacingM * 2
                    radius: Theme.cornerRadius
                    color: Theme.withAlpha(Theme.error, 0.14)

                    StyledText {
                        id: errorText
                        anchors.fill: parent
                        anchors.margins: Theme.spacingM
                        text: OpenVpn3Service.lastError
                        color: Theme.error
                        font.pixelSize: Theme.fontSizeSmall
                        wrapMode: Text.WordWrap
                    }
                }

                Item {
                    visible: OpenVpn3Service.available && OpenVpn3Service.profiles.length === 0
                    width: parent.width
                    height: visible ? emptyState.implicitHeight + Theme.spacingL * 2 : 0

                    Column {
                        id: emptyState
                        width: parent.width
                        spacing: Theme.spacingXS
                        anchors.centerIn: parent

                        DankIcon {
                            name: "vpn_key"
                            size: 28
                            color: Theme.surfaceVariantText
                            anchors.horizontalCenter: parent.horizontalCenter
                        }

                        StyledText {
                            width: parent.width
                            text: "No profiles"
                            color: Theme.surfaceText
                            font.pixelSize: Theme.fontSizeMedium
                            font.weight: Font.Medium
                            horizontalAlignment: Text.AlignHCenter
                        }

                        StyledText {
                            width: parent.width
                            text: "Import an .ovpn or .conf profile to get started."
                            color: Theme.surfaceVariantText
                            font.pixelSize: Theme.fontSizeSmall
                            horizontalAlignment: Text.AlignHCenter
                            wrapMode: Text.WordWrap
                        }
                    }
                }

                Repeater {
                    model: OpenVpn3Service.profiles

                    delegate: StyledRect {
                        id: profileCard
                        required property var modelData
                        readonly property var profileSessions: OpenVpn3Service.sessionsForProfile(modelData.path)
                        readonly property bool hasLiveSession: profileSessions.some(session => !["failed", "disconnected"].includes(session.state))

                        width: parent.width
                        height: profileColumn.implicitHeight + Theme.spacingM * 2
                        radius: Theme.cornerRadius
                        color: Theme.surfaceContainerHigh

                        Column {
                            id: profileColumn
                            anchors.left: parent.left
                            anchors.right: parent.right
                            anchors.top: parent.top
                            anchors.margins: Theme.spacingM
                            spacing: Theme.spacingS

                            RowLayout {
                                width: parent.width

                                Column {
                                    Layout.fillWidth: true
                                    spacing: 2

                                    StyledText {
                                        width: parent.width
                                        text: profileCard.modelData.name
                                        color: Theme.surfaceText
                                        font.pixelSize: Theme.fontSizeMedium
                                        font.weight: Font.Medium
                                        elide: Text.ElideRight
                                    }

                                    StyledText {
                                        visible: profileCard.profileSessions.length === 0
                                        text: "Disconnected"
                                        color: Theme.surfaceVariantText
                                        font.pixelSize: Theme.fontSizeSmall
                                    }
                                }

                                Rectangle {
                                    visible: !profileCard.hasLiveSession
                                    width: connectLabel.implicitWidth + Theme.spacingM * 2
                                    height: 30
                                    radius: 15
                                    color: connectArea.containsMouse ? Theme.primaryHoverLight : Theme.surfaceLight
                                    opacity: OpenVpn3Service.actionInFlight ? 0.5 : 1

                                    StyledText {
                                        id: connectLabel
                                        anchors.centerIn: parent
                                        text: "Connect"
                                        color: Theme.primary
                                        font.pixelSize: Theme.fontSizeSmall
                                        font.weight: Font.Medium
                                    }

                                    MouseArea {
                                        id: connectArea
                                        anchors.fill: parent
                                        hoverEnabled: true
                                        enabled: !OpenVpn3Service.actionInFlight
                                        cursorShape: Qt.PointingHandCursor
                                        onClicked: OpenVpn3Service.connectProfile(profileCard.modelData.path)
                                    }
                                }

                                DankActionButton {
                                    iconName: "delete"
                                    buttonSize: 30
                                    iconSize: 18
                                    iconColor: profileCard.hasLiveSession ? Theme.surfaceVariantText : Theme.error
                                    enabled: !profileCard.hasLiveSession && !OpenVpn3Service.actionInFlight
                                    opacity: enabled ? 1 : 0.5
                                    tooltipText: profileCard.hasLiveSession ? "Disconnect before removing" : "Remove profile"
                                    tooltipSide: "bottom"
                                    onClicked: {
                                        popout.pendingRemovePath = String(profileCard.modelData.path);
                                    }
                                }
                            }

                            StyledRect {
                                visible: popout.pendingRemovePath === profileCard.modelData.path
                                width: parent.width
                                height: removeRow.implicitHeight + Theme.spacingS * 2
                                radius: Theme.cornerRadius
                                color: Theme.withAlpha(Theme.error, 0.12)

                                RowLayout {
                                    id: removeRow
                                    anchors.fill: parent
                                    anchors.margins: Theme.spacingS
                                    spacing: Theme.spacingS

                                    StyledText {
                                        Layout.fillWidth: true
                                        text: `Remove “${profileCard.modelData.name}”?`
                                        color: Theme.surfaceText
                                        font.pixelSize: Theme.fontSizeSmall
                                        elide: Text.ElideRight
                                    }

                                    DankButton {
                                        text: "Cancel"
                                        onClicked: popout.pendingRemovePath = ""
                                    }

                                    DankButton {
                                        text: "Remove"
                                        enabled: !OpenVpn3Service.actionInFlight
                                        onClicked: {
                                            const configPath = popout.pendingRemovePath;
                                            popout.pendingRemovePath = "";
                                            OpenVpn3Service.removeProfile(configPath);
                                        }
                                    }
                                }
                            }

                            Repeater {
                                model: profileCard.profileSessions

                                delegate: RowLayout {
                                    required property var modelData
                                    width: profileColumn.width
                                    spacing: Theme.spacingS

                                    DankIcon {
                                        name: root.stateIcon(modelData.state)
                                        size: 18
                                        color: root.stateColor(modelData.state)
                                    }

                                    Column {
                                        Layout.fillWidth: true
                                        spacing: 1

                                        StyledText {
                                            width: parent.width
                                            text: root.stateLabel(modelData.state)
                                            color: modelData.active ? Theme.primary : Theme.surfaceText
                                            font.pixelSize: Theme.fontSizeSmall
                                            font.weight: Font.Medium
                                        }

                                        StyledText {
                                            visible: modelData.state === "auth_required" || modelData.device_name.length > 0 || modelData.message.length > 0
                                            width: parent.width
                                            text: modelData.state === "auth_required" && modelData.backend_pid > 0 ? `Run openvpn3 session-auth --auth-req ${modelData.backend_pid}` : [modelData.device_name, modelData.message].filter(value => value.length > 0).join(" • ")
                                            color: Theme.surfaceVariantText
                                            font.pixelSize: Theme.fontSizeSmall
                                            elide: Text.ElideRight
                                        }
                                    }

                                    Rectangle {
                                        width: disconnectLabel.implicitWidth + Theme.spacingM * 2
                                        height: 28
                                        radius: 14
                                        color: disconnectArea.containsMouse ? Theme.errorHover : Theme.surfaceLight
                                        opacity: OpenVpn3Service.actionInFlight ? 0.5 : 1

                                        StyledText {
                                            id: disconnectLabel
                                            anchors.centerIn: parent
                                            text: "Disconnect"
                                            color: Theme.error
                                            font.pixelSize: Theme.fontSizeSmall
                                        }

                                        MouseArea {
                                            id: disconnectArea
                                            anchors.fill: parent
                                            hoverEnabled: true
                                            enabled: !OpenVpn3Service.actionInFlight
                                            cursorShape: Qt.PointingHandCursor
                                            onClicked: OpenVpn3Service.disconnectSession(modelData.path)
                                        }
                                    }
                                }
                            }
                        }
                    }
                }

                Column {
                    width: parent.width
                    spacing: Theme.spacingXS
                    visible: orphanRepeater.count > 0

                    StyledText {
                        text: "Other sessions"
                        color: Theme.surfaceVariantText
                        font.pixelSize: Theme.fontSizeSmall
                        font.weight: Font.Medium
                    }

                    Repeater {
                        id: orphanRepeater
                        model: OpenVpn3Service.sessions.filter(session => !OpenVpn3Service.profiles.some(profile => profile.path === session.config_path))

                        delegate: RowLayout {
                            required property var modelData
                            width: parent.width

                            StyledText {
                                Layout.fillWidth: true
                                text: `${modelData.config_name || "Unknown profile"} • ${root.stateLabel(modelData.state)}`
                                color: Theme.surfaceText
                                font.pixelSize: Theme.fontSizeSmall
                            }

                            DankActionButton {
                                iconName: "link_off"
                                buttonSize: 28
                                iconColor: Theme.error
                                enabled: !OpenVpn3Service.actionInFlight
                                tooltipText: "Disconnect"
                                onClicked: OpenVpn3Service.disconnectSession(modelData.path)
                            }
                        }
                    }
                }
            }
            }
        }
    }
}
