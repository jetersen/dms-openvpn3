pragma Singleton
pragma ComponentBehavior: Bound

import QtQuick
import Quickshell
import Quickshell.Io
import qs.Common
import qs.Services

Singleton {
    id: root

    readonly property string helperPath: Paths.strip(Qt.resolvedUrl("helper/openvpn3_bridge.py"))
    readonly property string sessionService: "net.openvpn.v3.sessions"
    readonly property string sessionManagerPath: "/net/openvpn/v3/sessions"
    readonly property string statusSender: "net.openvpn.v3.log"
    readonly property string statusInterface: "net.openvpn.v3.backends"
    property var profiles: []
    property var sessions: []
    property var warnings: []
    property bool available: false
    property bool refreshInFlight: false
    property bool actionInFlight: false
    property string actionPath: ""
    property string actionKind: ""
    property string lastError: ""
    property bool refreshPending: false
    property string managerSubscriptionId: ""
    property var sessionSubscriptions: ({})

    readonly property var connectedSessions: sessions.filter(session => session.active === true)
    readonly property bool isConnected: connectedSessions.length > 0
    readonly property bool hasTransitionalSession: sessions.some(session => ["connecting", "disconnecting", "auth_required", "preparing"].includes(session.state))

    function sessionsForProfile(configPath) {
        return sessions.filter(session => session.config_path === configPath);
    }

    function stateFromStatus(major, minor) {
        if (major === 2 && minor === 7)
            return "connected";
        if ([5, 6, 12, 15].includes(minor))
            return "connecting";
        if (minor === 8)
            return "disconnecting";
        if ([13, 14].includes(minor))
            return "paused";
        if ([4, 20, 21, 22].includes(minor))
            return "auth_required";
        if ([1, 4].includes(major) || [1, 3, 10, 11, 23, 24, 25, 26].includes(minor))
            return "failed";
        if ([9, 16, 18, 19, 28, 29].includes(minor))
            return "disconnected";
        return "preparing";
    }

    function applyStatusSignal(data) {
        const path = data?.path || "";
        const body = data?.body || [];
        if (!path || body.length < 2)
            return;
        const index = sessions.findIndex(session => session.path === path);
        if (index < 0) {
            refresh();
            return;
        }
        const major = Number(body[0]);
        const minor = Number(body[1]);
        const state = stateFromStatus(major, minor);
        const next = sessions.slice();
        next[index] = Object.assign({}, next[index], {
            "major": major,
            "minor": minor,
            "state": state,
            "active": state === "connected",
            "message": state === "auth_required" ? "Authentication required" : String(body[2] || "")
        });
        sessions = next;
    }

    function applyAttentionSignal(data) {
        const path = data?.path || "";
        const index = sessions.findIndex(session => session.path === path);
        if (index < 0) {
            refresh();
            return;
        }
        const next = sessions.slice();
        next[index] = Object.assign({}, next[index], {
            "state": "auth_required",
            "active": false,
            "message": "Authentication required"
        });
        sessions = next;
    }

    function initializeSubscriptions() {
        if (!DMSService.isConnected || managerSubscriptionId)
            return;
        DMSService.dbusSubscribe("system", sessionService, sessionManagerPath, sessionService, "SessionManagerEvent", response => {
            if (response.error)
                return;
            managerSubscriptionId = response.result?.subscriptionId || "";
        });
    }

    function subscribeSession(path) {
        if (!DMSService.isConnected || sessionSubscriptions[path])
            return;
        sessionSubscriptions[path] = {
            "statusId": "",
            "attentionId": ""
        };
        DMSService.dbusSubscribe("system", statusSender, path, statusInterface, "StatusChange", response => {
            const subscriptionId = response.result?.subscriptionId || "";
            const entry = sessionSubscriptions[path];
            if (!entry) {
                if (subscriptionId)
                    DMSService.dbusUnsubscribe(subscriptionId, null);
                return;
            }
            if (response.error) {
                entry.statusFailed = true;
                return;
            }
            entry.statusId = subscriptionId;
            DMSService.dbusCall("system", sessionService, path, sessionService, "LogForward", [true], () => {});
        });
        DMSService.dbusSubscribe("system", sessionService, path, sessionService, "AttentionRequired", response => {
            const subscriptionId = response.result?.subscriptionId || "";
            const entry = sessionSubscriptions[path];
            if (!entry) {
                if (subscriptionId)
                    DMSService.dbusUnsubscribe(subscriptionId, null);
                return;
            }
            if (response.error) {
                entry.attentionFailed = true;
                return;
            }
            entry.attentionId = subscriptionId;
        });
    }

    function unsubscribeSession(path) {
        const entry = sessionSubscriptions[path];
        if (!entry)
            return;
        if (entry.statusId)
            DMSService.dbusUnsubscribe(entry.statusId, null);
        if (entry.attentionId)
            DMSService.dbusUnsubscribe(entry.attentionId, null);
        if (DMSService.isConnected)
            DMSService.dbusCall("system", sessionService, path, sessionService, "LogForward", [false], () => {});
        delete sessionSubscriptions[path];
    }

    function reconcileSessionSubscriptions() {
        if (!DMSService.isConnected)
            return;
        const currentPaths = new Set(sessions.map(session => session.path));
        for (const path of currentPaths)
            subscribeSession(path);
        for (const path of Object.keys(sessionSubscriptions)) {
            if (!currentPaths.has(path))
                unsubscribeSession(path);
        }
    }

    function resetSubscriptions() {
        managerSubscriptionId = "";
        sessionSubscriptions = ({});
    }

    function cleanupSubscriptions() {
        for (const path of Object.keys(sessionSubscriptions))
            unsubscribeSession(path);
        if (managerSubscriptionId)
            DMSService.dbusUnsubscribe(managerSubscriptionId, null);
        managerSubscriptionId = "";
    }

    function refresh() {
        if (refreshInFlight) {
            refreshPending = true;
            return;
        }
        refreshInFlight = true;
        snapshotTimeout.restart();
        snapshotProcess.running = true;
    }

    function connectProfile(configPath) {
        if (actionInFlight)
            return;
        actionInFlight = true;
        actionPath = configPath;
        actionKind = "connect";
        lastError = "";
        actionProcess.command = ["python3", helperPath, "connect", "--config-path", configPath];
        actionTimeout.restart();
        actionProcess.running = true;
    }

    function disconnectSession(sessionPath) {
        if (actionInFlight)
            return;
        actionInFlight = true;
        actionPath = sessionPath;
        actionKind = "disconnect";
        lastError = "";
        actionProcess.command = ["python3", helperPath, "disconnect", "--session-path", sessionPath];
        actionTimeout.restart();
        actionProcess.running = true;
    }

    function importProfile(filePath) {
        if (actionInFlight || !filePath)
            return;
        actionInFlight = true;
        actionPath = "";
        actionKind = "import";
        lastError = "";
        actionProcess.command = ["python3", helperPath, "import-profile", "--file-path", filePath];
        actionTimeout.restart();
        actionProcess.running = true;
    }

    function removeProfile(configPath) {
        if (actionInFlight)
            return;
        actionInFlight = true;
        actionPath = configPath;
        actionKind = "remove";
        lastError = "";
        ToastService.showInfo("Profiles", "Removing profile…");
        actionProcess.command = ["python3", helperPath, "remove-profile", "--config-path", configPath];
        actionTimeout.restart();
        actionProcess.running = true;
    }

    function parseResponse(text) {
        try {
            return JSON.parse(text.trim());
        } catch (error) {
            return {
                "ok": false,
                "error": {
                    "code": "INVALID_RESPONSE",
                    "message": "The helper returned invalid data"
                }
            };
        }
    }

    function responseError(response) {
        return response?.error?.message || "The operation failed";
    }

    Component.onCompleted: {
        initializeSubscriptions();
        refresh();
    }

    Component.onDestruction: cleanupSubscriptions()

    Connections {
        target: DMSService

        function onConnectionStateChanged() {
            if (!DMSService.isConnected) {
                root.resetSubscriptions();
                return;
            }
            root.initializeSubscriptions();
            root.refresh();
        }

        function onDbusSignalReceived(subscriptionId, data) {
            if (subscriptionId === root.managerSubscriptionId) {
                root.refresh();
                return;
            }
            for (const path of Object.keys(root.sessionSubscriptions)) {
                const entry = root.sessionSubscriptions[path];
                if (entry.statusId === subscriptionId) {
                    root.applyStatusSignal(data);
                    return;
                }
                if (entry.attentionId === subscriptionId) {
                    root.applyAttentionSignal(data);
                    return;
                }
            }
        }
    }

    Timer {
        interval: 60000
        repeat: true
        running: true
        onTriggered: root.refresh()
    }

    Timer {
        id: snapshotTimeout
        interval: 10000
        repeat: false
        onTriggered: {
            snapshotProcess.running = false;
            root.refreshInFlight = false;
            root.available = false;
            root.lastError = "Timed out while reading state";
        }
    }

    Timer {
        id: actionTimeout
        interval: 15000
        repeat: false
        onTriggered: {
            actionProcess.running = false;
            root.actionInFlight = false;
            root.actionPath = "";
            root.actionKind = "";
            root.lastError = "The operation timed out";
            ToastService.showError("Profiles", root.lastError);
            root.refresh();
        }
    }

    Process {
        id: snapshotProcess
        command: ["python3", root.helperPath, "snapshot"]
        running: false

        stdout: StdioCollector {
            onStreamFinished: {
                const response = root.parseResponse(text);
                if (response.ok) {
                    root.profiles = response.data.profiles || [];
                    root.sessions = response.data.sessions || [];
                    root.warnings = response.data.warnings || [];
                    root.available = true;
                    root.lastError = "";
                    Qt.callLater(root.reconcileSessionSubscriptions);
                } else {
                    root.available = false;
                    root.lastError = root.responseError(response);
                }
            }
        }

        onExited: {
            snapshotTimeout.stop();
            root.refreshInFlight = false;
            if (root.refreshPending) {
                root.refreshPending = false;
                Qt.callLater(root.refresh);
            }
        }
    }

    Process {
        id: actionProcess
        running: false

        stdout: StdioCollector {
            onStreamFinished: {
                const response = root.parseResponse(text);
                if (!response.ok) {
                    root.lastError = root.responseError(response);
                    ToastService.showError("Profiles", root.lastError);
                    return;
                }
                const session = response.data?.session;
                if (response.data?.authenticationRequired || session?.state === "auth_required") {
                    const pid = session?.backend_pid || 0;
                    const hint = pid > 0 ? `Run: openvpn3 session-auth --auth-req ${pid}` : "Complete authentication with the openvpn3 CLI";
                    root.lastError = `Authentication required. ${hint}`;
                    ToastService.showInfo("Profiles", root.lastError);
                } else if (root.actionKind === "import") {
                    ToastService.showInfo("Profiles", `Imported ${response.data?.profile?.name || "profile"}`);
                } else if (root.actionKind === "remove") {
                    ToastService.showInfo("Profiles", "Profile removed");
                }
            }
        }

        stderr: StdioCollector {
            onStreamFinished: {
                const message = text.trim();
                if (message)
                    console.warn(`[ProfileManager] helper stderr kind=${root.actionKind}: ${message}`);
            }
        }

        onExited: {
            actionTimeout.stop();
            root.actionInFlight = false;
            root.actionPath = "";
            root.actionKind = "";
            root.refresh();
        }
    }
}
