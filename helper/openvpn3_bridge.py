#!/usr/bin/env python3
"""Small JSON bridge between DankMaterialShell and OpenVPN® 3 Linux D-Bus."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import sys
from typing import Any

PROTOCOL_VERSION = 1
CONFIG_SERVICE = "net.openvpn.v3.configuration"
CONFIG_PATH = "/net/openvpn/v3/configuration"
CONFIG_INTERFACE = CONFIG_SERVICE
SESSION_SERVICE = "net.openvpn.v3.sessions"
SESSION_PATH = "/net/openvpn/v3/sessions"
SESSION_INTERFACE = SESSION_SERVICE
PROPERTIES_INTERFACE = "org.freedesktop.DBus.Properties"
PEER_INTERFACE = "org.freedesktop.DBus.Peer"

CONFIG_PATH_RE = re.compile(r"^/net/openvpn/v3/configuration/[A-Za-z0-9_]+$")
SESSION_PATH_RE = re.compile(r"^/net/openvpn/v3/sessions/[A-Za-z0-9_]+$")
PROFILE_EXTENSIONS = {".ovpn", ".conf"}
MAX_PROFILE_BYTES = 4 * 1024 * 1024

MAJOR_NAMES = {
    0: "UNSET",
    1: "CONFIG",
    2: "CONNECTION",
    3: "SESSION",
    4: "PKCS11",
    5: "PROCESS",
}
MINOR_NAMES = {
    0: "UNSET", 1: "CFG_ERROR", 2: "CFG_OK", 3: "CFG_INLINE_MISSING",
    4: "CFG_REQUIRE_USER", 5: "CONN_INIT", 6: "CONN_CONNECTING",
    7: "CONN_CONNECTED", 8: "CONN_DISCONNECTING", 9: "CONN_DISCONNECTED",
    10: "CONN_FAILED", 11: "CONN_AUTH_FAILED", 12: "CONN_RECONNECTING",
    13: "CONN_PAUSING", 14: "CONN_PAUSED", 15: "CONN_RESUMING",
    16: "CONN_DONE", 17: "SESS_NEW", 18: "SESS_BACKEND_COMPLETED",
    19: "SESS_REMOVED", 20: "SESS_AUTH_USERPASS", 21: "SESS_AUTH_CHALLENGE",
    22: "SESS_AUTH_URL", 23: "PKCS11_SIGN", 24: "PKCS11_ENCRYPT",
    25: "PKCS11_DECRYPT", 26: "PKCS11_VERIFY", 27: "PROC_STARTED",
    28: "PROC_STOPPED", 29: "PROC_KILLED",
}


class BridgeError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def state_from_status(major: int, minor: int) -> str:
    if major == 2 and minor == 7:
        return "connected"
    if minor in {5, 6, 12, 15}:
        return "connecting"
    if minor == 8:
        return "disconnecting"
    if minor in {13, 14}:
        return "paused"
    if minor in {4, 20, 21, 22}:
        return "auth_required"
    if major in {1, 4} or minor in {1, 3, 10, 11, 23, 24, 25, 26}:
        return "failed"
    if minor in {9, 16, 18, 19, 28, 29}:
        return "disconnected"
    return "preparing"


def json_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): json_value(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_value(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    return str(value)


def classify_exception(exc: Exception) -> BridgeError:
    name = getattr(exc, "get_dbus_name", lambda: "")()
    text = str(exc)
    lowered = f"{name} {text}".lower()
    if "accessdenied" in lowered or "access denied" in lowered:
        return BridgeError("ACCESS_DENIED", "The service denied access to this object")
    if "serviceunknown" in lowered or "namehasnoowner" in lowered or "no such service" in lowered:
        return BridgeError("SERVICE_UNAVAILABLE", "The required services are unavailable")
    if "unknownobject" in lowered or "not found" in lowered or "does not exist" in lowered:
        return BridgeError("NOT_FOUND", "The session object no longer exists")
    return BridgeError("DBUS_ERROR", text or "A D-Bus call failed")


class OpenVpnBus:
    def __init__(self, bus: Any):
        import dbus

        self.dbus = dbus
        self.bus = bus
        self.config_manager_object = bus.get_object(CONFIG_SERVICE, CONFIG_PATH)
        self.config_manager = dbus.Interface(self.config_manager_object, CONFIG_INTERFACE)
        self.session_manager_object = bus.get_object(SESSION_SERVICE, SESSION_PATH)
        self.session_manager = dbus.Interface(self.session_manager_object, SESSION_INTERFACE)

    def properties(self, service: str, path: str) -> Any:
        obj = self.bus.get_object(service, path)
        return self.dbus.Interface(obj, PROPERTIES_INTERFACE)

    def session_interface(self, path: str) -> Any:
        obj = self.bus.get_object(SESSION_SERVICE, path)
        return self.dbus.Interface(obj, SESSION_INTERFACE)

    def config_interface(self, path: str) -> Any:
        obj = self.bus.get_object(CONFIG_SERVICE, path)
        return self.dbus.Interface(obj, CONFIG_INTERFACE)

    def config_paths(self) -> list[str]:
        return [str(path) for path in self.config_manager.FetchAvailableConfigs()]

    def session_paths(self) -> list[str]:
        return [str(path) for path in self.session_manager.FetchAvailableSessions()]


def read_property(props: Any, interface: str, name: str, default: Any = None) -> Any:
    try:
        return props.Get(interface, name)
    except Exception:
        return default


def profile_record(api: OpenVpnBus, path: str) -> dict[str, Any]:
    props = api.properties(CONFIG_SERVICE, path)
    return {
        "path": path,
        "name": str(props.Get(CONFIG_INTERFACE, "name")),
        "valid": bool(read_property(props, CONFIG_INTERFACE, "valid", True)),
    }


def session_record(api: OpenVpnBus, path: str) -> dict[str, Any]:
    props = api.properties(SESSION_SERVICE, path)
    raw_status = props.Get(SESSION_INTERFACE, "status")
    status = list(raw_status) if raw_status is not None else [0, 0, ""]
    while len(status) < 3:
        status.append("")
    major, minor, message = int(status[0]), int(status[1]), str(status[2])
    state = state_from_status(major, minor)
    if state == "auth_required":
        message = "Authentication required"
    return {
        "path": path,
        "config_path": str(props.Get(SESSION_INTERFACE, "config_path")),
        "config_name": str(read_property(props, SESSION_INTERFACE, "config_name", "")),
        "major": major,
        "major_name": MAJOR_NAMES.get(major, f"UNKNOWN_{major}"),
        "minor": minor,
        "minor_name": MINOR_NAMES.get(minor, f"UNKNOWN_{minor}"),
        "state": state,
        "message": message,
        "active": state == "connected",
        "device_name": str(read_property(props, SESSION_INTERFACE, "device_name", "")),
        "session_name": str(read_property(props, SESSION_INTERFACE, "session_name", "")),
        "backend_pid": int(read_property(props, SESSION_INTERFACE, "backend_pid", 0) or 0),
    }


def snapshot(api: OpenVpnBus) -> dict[str, Any]:
    profiles: list[dict[str, Any]] = []
    sessions: list[dict[str, Any]] = []
    warnings: list[str] = []
    for path in api.config_paths():
        try:
            profiles.append(profile_record(api, path))
        except Exception as exc:
            warnings.append(f"Could not read configuration {path}: {classify_exception(exc).code}")
    for path in api.session_paths():
        try:
            sessions.append(session_record(api, path))
        except Exception as exc:
            warnings.append(f"Could not read session {path}: {classify_exception(exc).code}")
    profiles.sort(key=lambda item: item["name"].casefold())
    return {"profiles": profiles, "sessions": sessions, "warnings": warnings}


def health(api: OpenVpnBus) -> dict[str, Any]:
    config_peer = api.dbus.Interface(api.config_manager_object, PEER_INTERFACE)
    session_peer = api.dbus.Interface(api.session_manager_object, PEER_INTERFACE)
    config_peer.Ping()
    session_peer.Ping()
    return {"configurationService": True, "sessionService": True}


def connect(api: OpenVpnBus, config_path: str) -> dict[str, Any]:
    if not CONFIG_PATH_RE.fullmatch(config_path):
        raise BridgeError("INVALID_REQUEST", "Invalid configuration path")
    if config_path not in api.config_paths():
        raise BridgeError("NOT_FOUND", "The configuration is unavailable")

    for path in api.session_paths():
        record = session_record(api, path)
        if record["config_path"] == config_path and record["state"] not in {"failed", "disconnected"}:
            return {"session": record, "existing": True}

    new_path = ""
    try:
        new_path = str(api.session_manager.NewTunnel(api.dbus.ObjectPath(config_path)))
        interface = api.session_interface(new_path)
        try:
            interface.Ready()
        except Exception:
            record = session_record(api, new_path)
            if record["state"] == "auth_required":
                return {"session": record, "existing": False, "authenticationRequired": True}
            try:
                pending = interface.UserInputQueueGetTypeGroup()
            except Exception:
                pending = []
            if pending:
                record["state"] = "auth_required"
                return {"session": record, "existing": False, "authenticationRequired": True}
            raise
        interface.Connect()
        return {"session": session_record(api, new_path), "existing": False}
    except BridgeError:
        raise
    except Exception as exc:
        if new_path:
            try:
                api.session_interface(new_path).Disconnect()
            except Exception:
                pass
        raise classify_exception(exc) from exc


def disconnect(api: OpenVpnBus, session_path: str) -> dict[str, Any]:
    if not SESSION_PATH_RE.fullmatch(session_path):
        raise BridgeError("INVALID_REQUEST", "Invalid session path")
    if session_path not in api.session_paths():
        return {"disconnected": True, "alreadyGone": True}
    try:
        api.session_interface(session_path).Disconnect()
    except Exception as exc:
        error = classify_exception(exc)
        if error.code == "NOT_FOUND":
            return {"disconnected": True, "alreadyGone": True}
        raise error from exc
    return {"disconnected": True, "alreadyGone": False}


def read_profile_file(file_path: str) -> tuple[Path, str]:
    try:
        path = Path(file_path).expanduser().resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise BridgeError("INVALID_FILE", "The selected profile could not be opened") from exc
    if not path.is_file():
        raise BridgeError("INVALID_FILE", "The selected profile is not a regular file")
    if path.suffix.lower() not in PROFILE_EXTENSIONS:
        raise BridgeError("INVALID_FILE", "Select an .ovpn or .conf profile")
    try:
        size = path.stat().st_size
        if size > MAX_PROFILE_BYTES:
            raise BridgeError("INVALID_FILE", "The selected profile is larger than 4 MiB")
        contents = path.read_bytes()
        if len(contents) > MAX_PROFILE_BYTES:
            raise BridgeError("INVALID_FILE", "The selected profile is larger than 4 MiB")
    except BridgeError:
        raise
    except OSError as exc:
        raise BridgeError("INVALID_FILE", "The selected profile could not be read") from exc
    if b"\0" in contents:
        raise BridgeError("INVALID_FILE", "The selected profile is not a text file")
    try:
        return path, contents.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise BridgeError("INVALID_FILE", "The selected profile is not UTF-8 text") from exc


def import_profile(api: OpenVpnBus, file_path: str, name: str | None = None) -> dict[str, Any]:
    path, contents = read_profile_file(file_path)
    profile_name = (name if name is not None else path.stem).strip()
    if not profile_name or len(profile_name) > 128 or any(character in profile_name for character in "\r\n\0"):
        raise BridgeError("INVALID_REQUEST", "The profile name must be 1 to 128 characters")
    try:
        config_path = str(api.config_manager.Import(profile_name, contents, False, True))
        if not CONFIG_PATH_RE.fullmatch(config_path):
            raise BridgeError("DBUS_ERROR", "The service returned an invalid configuration path")
        return {"profile": profile_record(api, config_path)}
    except BridgeError:
        raise
    except Exception as exc:
        error = classify_exception(exc)
        if error.code == "DBUS_ERROR":
            error = BridgeError("IMPORT_FAILED", "The service could not import this profile; verify that it is a valid inline configuration")
        raise error from exc


def remove_profile(api: OpenVpnBus, config_path: str) -> dict[str, Any]:
    if not CONFIG_PATH_RE.fullmatch(config_path):
        raise BridgeError("INVALID_REQUEST", "Invalid configuration path")
    if config_path not in api.config_paths():
        return {"removed": True, "alreadyGone": True}
    for path in api.session_paths():
        record = session_record(api, path)
        if record["config_path"] == config_path and record["state"] not in {"failed", "disconnected"}:
            raise BridgeError("PROFILE_IN_USE", "Disconnect this profile before removing it")
    try:
        api.config_interface(config_path).Remove()
    except Exception as exc:
        error = classify_exception(exc)
        if error.code == "NOT_FOUND":
            return {"removed": True, "alreadyGone": True}
        raise error from exc
    return {"removed": True, "alreadyGone": False}


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    subparsers = result.add_subparsers(dest="operation", required=True)
    subparsers.add_parser("health")
    subparsers.add_parser("snapshot")
    connect_parser = subparsers.add_parser("connect")
    connect_parser.add_argument("--config-path", required=True)
    disconnect_parser = subparsers.add_parser("disconnect")
    disconnect_parser.add_argument("--session-path", required=True)
    import_parser = subparsers.add_parser("import-profile")
    import_parser.add_argument("--file-path", required=True)
    import_parser.add_argument("--name")
    remove_parser = subparsers.add_parser("remove-profile")
    remove_parser.add_argument("--config-path", required=True)
    return result


def run(argv: list[str] | None = None, bus: Any = None) -> dict[str, Any]:
    args = parser().parse_args(argv)
    if bus is None:
        try:
            import dbus
        except ImportError as exc:
            raise BridgeError("DEPENDENCY_MISSING", "Python dbus bindings are not installed") from exc
        bus = dbus.SystemBus()
    api = OpenVpnBus(bus)
    if args.operation == "health":
        return health(api)
    if args.operation == "snapshot":
        return snapshot(api)
    if args.operation == "connect":
        return connect(api, args.config_path)
    if args.operation == "disconnect":
        return disconnect(api, args.session_path)
    if args.operation == "import-profile":
        return import_profile(api, args.file_path, args.name)
    if args.operation == "remove-profile":
        return remove_profile(api, args.config_path)
    raise BridgeError("INVALID_REQUEST", "Unknown operation")


def main(argv: list[str] | None = None) -> int:
    try:
        data = run(argv)
        response = {"version": PROTOCOL_VERSION, "ok": True, "data": json_value(data)}
        exit_code = 0
    except BridgeError as exc:
        response = {"version": PROTOCOL_VERSION, "ok": False, "error": {"code": exc.code, "message": str(exc)}}
        exit_code = 1
    except Exception as exc:
        error = classify_exception(exc)
        response = {"version": PROTOCOL_VERSION, "ok": False, "error": {"code": error.code, "message": str(error)}}
        exit_code = 1
    sys.stdout.write(json.dumps(response, separators=(",", ":")) + "\n")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
