import json
from pathlib import Path
import subprocess
import unittest


class PolkitRuleTests(unittest.TestCase):
    def test_only_local_active_user_and_tunnel_dns_operations_are_allowed(self):
        rule = (Path(__file__).resolve().parents[1] / "docs/10-dms-openvpn3-dns.rules").read_text()
        script = """
const vm = require('node:vm');
const assert = require('node:assert/strict');
let rule;
vm.runInNewContext(SOURCE, {polkit: {addRule: fn => rule = fn, Result: {YES: 'yes'}}});
const subject = {user: 'REPLACE_WITH_USERNAME', local: true, active: true};
const action = (name, iface) => ({id: 'org.freedesktop.resolve1.' + name, lookup: () => iface});
for (const iface of ['tun0', 'tun1', 'tun12']) {
    for (const name of ['set-domains', 'set-default-route'])
        assert.equal(rule(action(name, iface), subject), 'yes');
}
for (const iface of ['eth0', 'wlan0', 'tailscale0', 'tun', 'tun0bad', undefined])
    assert.equal(rule(action('set-domains', iface), subject), undefined);
assert.equal(rule(action('set-dns-servers', 'tun0'), subject), undefined);
for (const change of [{user: 'someone-else'}, {local: false}, {active: false}])
    assert.equal(rule(action('set-domains', 'tun0'), {...subject, ...change}), undefined);
""".replace('SOURCE', json.dumps(rule))
        subprocess.run(['node', '-e', script], check=True)
