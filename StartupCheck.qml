import QtQuick
import Quickshell
import qs.Common

QtObject {
    readonly property string helperPath: Paths.strip(Qt.resolvedUrl("helper/openvpn3_bridge.py"))

    function check(done) {
        Proc.runCommand("openvpn3-plugin-health", ["python3", helperPath, "health"], (output, exitCode) => {
            if (exitCode === 0) {
                done(null);
                return;
            }
            let details = "Install the openvpn3 package, Python 3, and the Python D-Bus bindings.";
            try {
                const response = JSON.parse((output || "").trim());
                details = response?.error?.message || details;
            } catch (error) {
            }
            done({
                "title": "The required service is unavailable",
                "details": details
            });
        });
    }
}
