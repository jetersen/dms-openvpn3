#!/usr/bin/python3
"""Optional domain providers and split DNS for DMS OpenVPN 3."""

import json
import os
from pathlib import Path
import re
import socket
import subprocess
import fcntl
import tempfile


def state_path():
    runtime = os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{os.getuid()}")
    return Path(runtime) / "dms-openvpn3-dns/state.json"

SESSIONS = "net.openvpn.v3.sessions"
RESOLVED = "org.freedesktop.resolve1"
PROPERTIES = "org.freedesktop.DBus.Properties"


def config_path():
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config")) / "dms-openvpn3/dns.json"


def read_config(path):
    if not path.exists():
        return {}
    config = json.loads(path.read_text())
    if not isinstance(config, dict):
        raise ValueError("DNS configuration must be an object")
    profiles = config.get("profiles", {})
    if not isinstance(profiles, dict) or any(not isinstance(source, dict) for source in profiles.values()):
        raise ValueError("DNS profiles must be an object")
    return profiles


def save_state(path, state):
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(state))
    temporary.replace(path)


def parse_domains(text):
    domains = set()
    for line in text.splitlines():
        domain = line.split("#", 1)[0].strip().lower().removeprefix("~").rstrip(".")
        if not domain:
            continue
        if len(domain) > 253 or "." not in domain or any(
                not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
                for label in domain.split(".")):
            raise ValueError(f"Invalid DNS routing domain: {domain}")
        domains.add(domain)
    if not domains or len(domains) > 4096:
        raise ValueError("DNS provider must return 1–4096 domains")
    return sorted(domains)


def provider_domains(source):
    if ("file" in source) == ("command" in source):
        raise ValueError("Set exactly one DNS source: file or command")
    if "file" in source:
        path = Path(source["file"]).expanduser()
        with path.open() as stream:
            text = stream.read(1024 * 1024 + 1)
        if len(text) > 1024 * 1024:
            raise ValueError("DNS provider output exceeds 1 MiB")
        if path.suffix.lower() == ".json":
            values = json.loads(text)
            if not isinstance(values, list) or any(not isinstance(value, str) or
                                                  "\n" in value or "\r" in value for value in values):
                raise ValueError("DNS JSON file must contain an array of domain strings")
            text = "\n".join(values)
    else:
        command = source["command"]
        if not isinstance(command, list) or not command or any(
                not isinstance(arg, str) or not arg for arg in command):
            raise ValueError("DNS command must be an argument array")
        with tempfile.TemporaryFile() as output, tempfile.TemporaryFile() as errors:
            subprocess.run(command, stdout=output, stderr=errors, timeout=10, check=True)
            output.seek(0)
            text = output.read(1024 * 1024 + 1).decode("utf-8")
    if len(text) > 1024 * 1024:
        raise ValueError("DNS provider output exceeds 1 MiB")
    return parse_domains(text)


class Resolved:
    def __init__(self, bus):
        import dbus
        self.dbus = dbus
        self.bus = bus
        self.manager = dbus.Interface(bus.get_object(RESOLVED, "/org/freedesktop/resolve1"), RESOLVED + ".Manager")

    def read(self, device):
        index = socket.if_nametoindex(device)
        path = self.manager.GetLink(index)
        props = self.dbus.Interface(self.bus.get_object(RESOLVED, path), PROPERTIES)
        values = props.GetAll(RESOLVED + ".Link")
        return {"index": index, "domains": [[str(d), bool(route)] for d, route in values["Domains"]],
                "default": bool(values["DefaultRoute"]), "has_dns": bool(values["DNS"])}

    def write(self, device, domains, default):
        # resolvectl uses the existing desktop polkit agent when authorization
        # is needed. The provider command itself never runs with elevation.
        arguments = [("~" if route else "") + domain for domain, route in domains]
        current = self.read(device)
        commands = []
        if current["domains"] != domains:
            commands.append(["resolvectl", "domain", device, *(arguments or [""])])
        if current["default"] != default:
            commands.append(["resolvectl", "default-route", device, "yes" if default else "no"])
        for command in commands:
            try:
                subprocess.run(command, check=True, capture_output=True, text=True, timeout=60)
            except subprocess.CalledProcessError as error:
                raise RuntimeError(error.stderr.strip()[:300] or "Could not update DNS routing") from error


def restore(resolver, record):
    try:
        current = resolver.read(record["device"])
    except OSError:
        return
    if current["index"] != record["index"]:
        return
    if current["domains"] == record["applied"]:
        resolver.write(record["device"], record["domains"], record["default"])


def sync(profiles, sessions, resolver, path=None):
    """Called by the plugin on session events and initial load."""
    path = path or state_path()
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    with path.with_suffix(".lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        state = json.loads(path.read_text()) if path.exists() else {}
        statuses = {}
        active = {s["path"]: s for s in sessions if s["active"] and s["device_name"]
                  and isinstance(profiles.get(s["config_name"]), dict)
                  and profiles[s["config_name"]].get("enabled", False)}
        for session_path in list(state):
            if session_path not in active:
                record = state[session_path]
                try:
                    restore(resolver, record)
                    del state[session_path]
                except Exception as error:
                    statuses[record["profile"]] = {"state": "error", "error": str(error)}
        save_state(path, state)
        for name, source in profiles.items():
            statuses.setdefault(name, {"state": "waiting" if source.get("enabled", False) else "disabled"})
        for session_path, session in active.items():
            name, device = session["config_name"], session["device_name"]
            try:
                domains = [[d, True] for d in provider_domains(profiles[name])]
                current = resolver.read(device)
                if not current["has_dns"]:
                    statuses[name] = {"state": "waiting"}
                    continue
                record = state.get(session_path)
                if not record or record["index"] != current["index"] or record["device"] != device:
                    record = dict(current, device=device, profile=name, applied=domains)
                    state[session_path] = record
                # Persist rollback information before the first mutation.
                previous_applied = record.get("applied", domains)
                record["applied"] = domains
                save_state(path, state)
                if current["domains"] != domains or current["default"]:
                    try:
                        resolver.write(device, domains, False)
                    except Exception:
                        # If no mutation occurred, retain the previous cleanup
                        # target rather than losing ownership of existing routes.
                        latest = resolver.read(device)
                        if latest["index"] == current["index"] and latest["domains"] == previous_applied:
                            record["applied"] = previous_applied
                        raise
                statuses[name] = {"state": "applied", "domains": len(domains), "device": device}
            except Exception as error:
                statuses[name] = {"state": "error", "error": str(error)}
        save_state(path, state)
        return statuses
