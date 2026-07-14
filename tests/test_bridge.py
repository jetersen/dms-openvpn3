import importlib.util
import pathlib
import tempfile
import unittest


MODULE_PATH = pathlib.Path(__file__).parents[1] / "helper" / "openvpn3_bridge.py"
SPEC = importlib.util.spec_from_file_location("openvpn3_bridge", MODULE_PATH)
bridge = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(bridge)


class FakeProperties:
    def __init__(self, values):
        self.values = values

    def Get(self, interface, name):
        value = self.values[name]
        if isinstance(value, Exception):
            raise value
        return value


class FakeApi:
    def __init__(self):
        self._config_paths = ["/net/openvpn/v3/configuration/a"]
        self._session_paths = ["/net/openvpn/v3/sessions/b"]
        self._properties = {
            (bridge.CONFIG_SERVICE, self._config_paths[0]): FakeProperties({"name": "Work", "valid": True}),
            (bridge.SESSION_SERVICE, self._session_paths[0]): FakeProperties({
                "status": (2, 7, "Connected"),
                "config_path": self._config_paths[0],
                "config_name": "Work",
                "device_name": "tun0",
                "session_name": "session",
                "backend_pid": 123,
            }),
        }

    def config_paths(self):
        return list(self._config_paths)

    def session_paths(self):
        return list(self._session_paths)

    def properties(self, service, path):
        return self._properties[(service, path)]


class FakeDbus:
    @staticmethod
    def ObjectPath(value):
        return ("object-path", value)


class FakeSessionInterface:
    def __init__(self, ready_error=None, pending=None):
        self.ready_error = ready_error
        self.pending = pending or []
        self.ready_called = False
        self.connect_called = False
        self.disconnect_called = False

    def Ready(self):
        self.ready_called = True
        if self.ready_error:
            raise self.ready_error

    def Connect(self):
        self.connect_called = True

    def Disconnect(self):
        self.disconnect_called = True

    def UserInputQueueGetTypeGroup(self):
        return self.pending


class FakeSessionManager:
    def __init__(self, api):
        self.api = api
        self.new_tunnel_args = []

    def NewTunnel(self, config_path):
        self.new_tunnel_args.append(config_path)
        self.api._session_paths.append(self.api.new_session_path)
        return self.api.new_session_path


class MutationApi:
    config_path = "/net/openvpn/v3/configuration/cfg"
    new_session_path = "/net/openvpn/v3/sessions/new"

    def __init__(self, status=(2, 6, "Connecting"), ready_error=None, pending=None):
        self.dbus = FakeDbus()
        self._session_paths = []
        self.interface = FakeSessionInterface(ready_error, pending)
        self.session_manager = FakeSessionManager(self)
        self.props = FakeProperties({
            "status": status,
            "config_path": self.config_path,
            "config_name": "Work",
            "device_name": "",
            "session_name": "",
            "backend_pid": 77,
        })

    def config_paths(self):
        return [self.config_path]

    def session_paths(self):
        return list(self._session_paths)

    def properties(self, service, path):
        return self.props

    def session_interface(self, path):
        return self.interface


class FakeConfigManager:
    def __init__(self, api):
        self.api = api
        self.import_args = []

    def Import(self, name, contents, single_use, persistent):
        self.import_args.append((name, contents, single_use, persistent))
        self.api._config_paths.append(self.api.config_path)
        return self.api.config_path


class FakeConfigInterface:
    def __init__(self):
        self.remove_called = False

    def Remove(self):
        self.remove_called = True


class ProfileApi:
    config_path = "/net/openvpn/v3/configuration/imported"
    session_path = "/net/openvpn/v3/sessions/profile"

    def __init__(self, with_profile=False, session_status=None):
        self._config_paths = [self.config_path] if with_profile else []
        self._session_paths = [self.session_path] if session_status else []
        self.config_manager = FakeConfigManager(self)
        self.interface = FakeConfigInterface()
        self.config_props = FakeProperties({"name": "Imported", "valid": True})
        self.session_props = FakeProperties({
            "status": session_status or (2, 9, "Disconnected"),
            "config_path": self.config_path,
            "config_name": "Imported",
            "device_name": "",
            "session_name": "",
            "backend_pid": 0,
        })

    def config_paths(self):
        return list(self._config_paths)

    def session_paths(self):
        return list(self._session_paths)

    def properties(self, service, path):
        return self.config_props if service == bridge.CONFIG_SERVICE else self.session_props

    def config_interface(self, path):
        return self.interface


class BridgeTests(unittest.TestCase):
    def test_only_connected_status_is_active(self):
        self.assertEqual("connected", bridge.state_from_status(2, 7))
        self.assertNotEqual("connected", bridge.state_from_status(2, 6))
        self.assertNotEqual("connected", bridge.state_from_status(3, 17))

    def test_status_normalization(self):
        self.assertEqual("connecting", bridge.state_from_status(2, 12))
        self.assertEqual("disconnecting", bridge.state_from_status(2, 8))
        self.assertEqual("auth_required", bridge.state_from_status(3, 22))
        self.assertEqual("failed", bridge.state_from_status(2, 11))
        self.assertEqual("disconnected", bridge.state_from_status(2, 16))

    def test_snapshot_joins_by_config_path(self):
        result = bridge.snapshot(FakeApi())
        self.assertEqual("Work", result["profiles"][0]["name"])
        self.assertEqual(result["profiles"][0]["path"], result["sessions"][0]["config_path"])
        self.assertTrue(result["sessions"][0]["active"])

    def test_auth_url_status_is_not_exposed(self):
        api = FakeApi()
        session_props = api._properties[(bridge.SESSION_SERVICE, api._session_paths[0])]
        session_props.values["status"] = (3, 22, "https://login.example.test/secret-token")
        record = bridge.session_record(api, api._session_paths[0])
        self.assertEqual("auth_required", record["state"])
        self.assertEqual("Authentication required", record["message"])
        self.assertNotIn("secret-token", str(record))

    def test_path_validation(self):
        self.assertIsNotNone(bridge.CONFIG_PATH_RE.fullmatch("/net/openvpn/v3/configuration/abc_123"))
        self.assertIsNone(bridge.CONFIG_PATH_RE.fullmatch("/tmp/not-openvpn"))
        self.assertIsNotNone(bridge.SESSION_PATH_RE.fullmatch("/net/openvpn/v3/sessions/abc_123"))

    def test_json_value_normalizes_nested_values(self):
        class Value:
            def __str__(self):
                return "value"

        self.assertEqual({"items": ["value", 2]}, bridge.json_value({"items": [Value(), 2]}))

    def test_connect_uses_object_path_then_ready_and_connect(self):
        api = MutationApi()
        result = bridge.connect(api, api.config_path)
        self.assertEqual([("object-path", api.config_path)], api.session_manager.new_tunnel_args)
        self.assertTrue(api.interface.ready_called)
        self.assertTrue(api.interface.connect_called)
        self.assertEqual("connecting", result["session"]["state"])

    def test_existing_live_session_prevents_duplicate_tunnel(self):
        api = MutationApi(status=(2, 7, "Connected"))
        api._session_paths.append(api.new_session_path)
        result = bridge.connect(api, api.config_path)
        self.assertTrue(result["existing"])
        self.assertEqual([], api.session_manager.new_tunnel_args)

    def test_auth_required_session_is_retained(self):
        api = MutationApi(status=(3, 20, "Credentials"), ready_error=RuntimeError("credentials"))
        result = bridge.connect(api, api.config_path)
        self.assertTrue(result["authenticationRequired"])
        self.assertFalse(api.interface.disconnect_called)

    def test_non_auth_ready_failure_cleans_up_session(self):
        api = MutationApi(status=(3, 17, "New"), ready_error=RuntimeError("setup failed"))
        with self.assertRaises(bridge.BridgeError):
            bridge.connect(api, api.config_path)
        self.assertTrue(api.interface.disconnect_called)

    def test_disconnect_is_idempotent_when_session_is_gone(self):
        api = MutationApi()
        result = bridge.disconnect(api, api.new_session_path)
        self.assertTrue(result["alreadyGone"])

    def test_import_is_persistent_and_does_not_return_profile_contents(self):
        api = ProfileApi()
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "company.ovpn"
            path.write_text("client\n# private-marker\n", encoding="utf-8")
            result = bridge.import_profile(api, str(path))
        self.assertEqual(("company", "client\n# private-marker\n", False, True), api.config_manager.import_args[0])
        self.assertEqual(api.config_path, result["profile"]["path"])
        self.assertNotIn("private-marker", str(result))

    def test_import_rejects_non_openvpn_extension(self):
        api = ProfileApi()
        with tempfile.TemporaryDirectory() as directory:
            path = pathlib.Path(directory) / "company.txt"
            path.write_text("client\n", encoding="utf-8")
            with self.assertRaisesRegex(bridge.BridgeError, r"\.ovpn or \.conf"):
                bridge.import_profile(api, str(path))

    def test_remove_profile_calls_config_remove(self):
        api = ProfileApi(with_profile=True)
        result = bridge.remove_profile(api, api.config_path)
        self.assertTrue(result["removed"])
        self.assertTrue(api.interface.remove_called)

    def test_remove_profile_rejects_active_session(self):
        api = ProfileApi(with_profile=True, session_status=(2, 7, "Connected"))
        with self.assertRaises(bridge.BridgeError) as raised:
            bridge.remove_profile(api, api.config_path)
        self.assertEqual("PROFILE_IN_USE", raised.exception.code)
        self.assertFalse(api.interface.remove_called)


if __name__ == "__main__":
    unittest.main()
