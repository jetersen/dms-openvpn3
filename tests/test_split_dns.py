import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

SCRIPT = Path(__file__).resolve().parents[1] / "helper/split_dns.py"
spec = importlib.util.spec_from_file_location("split_dns", SCRIPT)
dns = importlib.util.module_from_spec(spec)
spec.loader.exec_module(dns)


class FakeResolver:
    def __init__(self):
        self.value = {"index": 48, "domains": [[".", True]], "default": True, "has_dns": True}
        self.writes = []
        self.exists = True

    def read(self, device):
        if not self.exists:
            raise OSError("Interface removed")
        return self.value.copy()

    def write(self, device, domains, default):
        self.writes.append((device, domains, default))
        self.value.update(domains=domains, default=default)


class ProviderTests(unittest.TestCase):
    def test_reads_generated_json_without_running_a_command(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "domains.json"
            path.write_text('["Example.COM", "foo.example.com"]')
            with patch.object(dns.subprocess, "run") as run:
                self.assertEqual(dns.provider_domains({"file": str(path)}),
                                 ["example.com", "foo.example.com"])
            run.assert_not_called()

    def test_rejects_invalid_json_lists(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "domains.json"
            for text in ('{"domains": []}', '[42]', '[]', '["foo.example\\nbar.example"]'):
                path.write_text(text)
                with self.assertRaises(ValueError):
                    dns.provider_domains({"file": str(path)})

    def test_domains_are_validated_and_normalized(self):
        self.assertEqual(dns.parse_domains("# comment\n~Example.COM.\nexample.com\nfoo.example.com\n"),
                         ["example.com", "foo.example.com"])
        for text in ("~.", "*.example.com", "-bad.example", "example..com", "", "1.2.3.4/24"):
            with self.assertRaises(ValueError):
                dns.parse_domains(text)

    def test_command_runs_without_shell(self):
        self.assertEqual(dns.provider_domains({"command": ["/usr/bin/printf", "example.com\\n"]}), ["example.com"])

    def test_provider_failures_are_reported(self):
        for source in ({}, {"file": "x", "command": ["x"]}, {"command": "echo example.com"}):
            with self.assertRaises(ValueError):
                dns.provider_domains(source)
        with self.assertRaises(dns.subprocess.CalledProcessError):
            dns.provider_domains({"command": ["/usr/bin/false"]})


class PermissionTests(unittest.TestCase):
    def test_tunnel_scope_needs_only_domain_authorization(self):
        resolver = object.__new__(dns.Resolved)
        with patch.object(resolver, "read", return_value={"domains": [], "default": False}), \
                patch.object(dns.subprocess, "run") as run:
            resolver.write("tun0", [["example.com", True]], False)
        self.assertEqual(run.call_count, 1)
        self.assertEqual(run.call_args.args[0], ["resolvectl", "domain", "tun0", "~example.com"])

    def test_matching_settings_need_no_authorization(self):
        resolver = object.__new__(dns.Resolved)
        with patch.object(resolver, "read", return_value={"domains": [["example.com", True]], "default": False}), \
                patch.object(dns.subprocess, "run") as run:
            resolver.write("tun0", [["example.com", True]], False)
        run.assert_not_called()


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / "state.json"
        self.source = Path(self.temp.name) / "domains"
        self.source.write_text("example.com\n")
        self.profiles = {"work": {"enabled": True, "file": str(self.source)}}
        self.session = {"path": "/session/one", "config_name": "work", "device_name": "tun0", "active": True}
        self.resolver = FakeResolver()

    def sync(self, sessions=None):
        return dns.sync(self.profiles, [self.session] if sessions is None else sessions, self.resolver, self.path)

    def test_applies_once_and_repairs_resets(self):
        self.assertEqual(self.sync()["work"]["state"], "applied")
        self.assertEqual(self.resolver.value["domains"], [["example.com", True]])
        self.assertFalse(self.resolver.value["default"])
        self.resolver.writes.clear()
        self.sync()
        self.assertEqual(self.resolver.writes, [])
        self.resolver.value["domains"] = [[".", True]]
        self.sync()
        self.assertEqual(len(self.resolver.writes), 1)

    def test_disconnect_restores_existing_interface(self):
        self.sync()
        self.sync([])
        self.assertEqual(self.resolver.value["domains"], [[".", True]])
        self.assertTrue(self.resolver.value["default"])

    def test_removed_or_reused_interface_is_not_modified(self):
        self.sync()
        self.resolver.writes.clear()
        self.resolver.exists = False
        self.sync([])
        self.assertEqual(self.resolver.writes, [])
        self.resolver.exists = True
        self.sync()
        self.resolver.writes.clear()
        self.resolver.value["index"] = 49
        self.sync([])
        self.assertEqual(self.resolver.writes, [])

    def test_does_not_overwrite_external_change_on_disconnect(self):
        self.sync()
        self.resolver.value["domains"] = [["other.example", True]]
        self.resolver.writes.clear()
        self.sync([])
        self.assertEqual(self.resolver.writes, [])

    def test_disabled_and_other_profiles_are_untouched(self):
        self.profiles["work"]["enabled"] = False
        self.sync()
        self.assertEqual(self.resolver.writes, [])
        self.profiles["work"]["enabled"] = True
        self.session["config_name"] = "other"
        self.sync()
        self.assertEqual(self.resolver.writes, [])

    def test_waits_for_connected_session_and_dns(self):
        self.session["active"] = False
        self.sync()
        self.session["active"] = True
        self.resolver.value["has_dns"] = False
        self.sync()
        self.assertEqual(self.resolver.writes, [])

    def test_provider_error_preserves_applied_routes(self):
        self.sync()
        self.source.write_text("invalid")
        self.resolver.writes.clear()
        self.assertEqual(self.sync()["work"]["state"], "error")
        self.assertEqual(self.resolver.writes, [])
        self.sync([])
        self.assertEqual(self.resolver.value["domains"], [[".", True]])

    def test_permission_denied_is_visible(self):
        with patch.object(self.resolver, "write", side_effect=PermissionError("Denied")):
            result = self.sync()
        self.assertEqual(result["work"]["state"], "error")
        self.assertIn("Denied", result["work"]["error"])

    def test_failed_provider_update_retains_previous_cleanup_target(self):
        self.sync()
        self.source.write_text("changed.example\n")
        with patch.object(self.resolver, "write", side_effect=PermissionError("Denied")):
            self.sync()
        self.sync([])
        self.assertEqual(self.resolver.value["domains"], [[".", True]])

    def test_disable_restores_routes(self):
        self.sync()
        self.profiles["work"]["enabled"] = False
        self.assertEqual(self.sync()["work"]["state"], "disabled")
        self.assertEqual(self.resolver.value["domains"], [[".", True]])


if __name__ == "__main__":
    unittest.main()
