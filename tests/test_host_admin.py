import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import host_admin


class HostAdminTests(unittest.TestCase):
    def test_nameservers_accept_only_unique_safe_ip_addresses(self):
        self.assertEqual(host_admin._validate_nameservers(["1.1.1.1", "2606:4700:4700::1111"]),
                         ["1.1.1.1", "2606:4700:4700::1111"])
        for values in (["1.1.1.1; reboot"], ["0.0.0.0"], ["1.1.1.1", "1.1.1.1"]):
            with self.assertRaises(ValueError):
                host_admin._validate_nameservers(values)

    def test_static_resolv_conf_preserves_options_and_records_managed_state(self):
        with tempfile.TemporaryDirectory() as directory:
            resolv = Path(directory) / "resolv.conf"
            state = Path(directory) / "nameservers.json"
            resolv.write_text("search example.test\noptions timeout:2\nnameserver 9.9.9.9\n")
            with mock.patch.object(host_admin, "RESOLV_CONF", resolv), \
                 mock.patch.object(host_admin, "DNS_STATE", state), \
                 mock.patch.object(host_admin.shutil, "which", return_value=None):
                host_admin.set_nameservers(["1.1.1.1", "8.8.8.8"])
                inventory = host_admin.get_nameservers()
            content = resolv.read_text()
            self.assertIn("options timeout:2", content)
            self.assertNotIn("9.9.9.9", content)
            self.assertEqual(inventory["managed"], ["1.1.1.1", "8.8.8.8"])

    def test_firewall_rule_is_normalized_without_shell_text(self):
        rule = host_admin._normalize_firewall_rule({
            "action": "accept", "protocol": "tcp", "source": "10.0.0.5/24",
            "port": "8000-8100", "description": "private app"})
        self.assertEqual(rule["source"], "10.0.0.0/24")
        self.assertEqual(rule["port"], "8000:8100")
        with self.assertRaises(ValueError):
            host_admin._normalize_firewall_rule({
                "action": "accept", "protocol": "tcp", "source": "0.0.0.0/0",
                "port": "22; reboot"})

    def test_firewall_always_keeps_ssh_and_rolls_back_on_failure(self):
        calls = []

        def run(command, **kwargs):
            calls.append((command, kwargs.get("input_text")))
            if command == ["iptables-save"]:
                return mock.Mock(returncode=0, stdout="original")
            if command[:3] == ["iptables", "-A", host_admin.FIREWALL_CHAIN] and "--dport" in command and "443" in command:
                raise RuntimeError("failed")
            return mock.Mock(returncode=0, stdout="")

        with mock.patch.object(host_admin.shutil, "which", return_value="/sbin/tool"), \
             mock.patch.object(host_admin, "_run", side_effect=run), \
             self.assertRaises(RuntimeError):
            host_admin.set_firewall({"enabled": True, "default": "deny", "rules": [
                {"action": "accept", "protocol": "tcp", "source": "0.0.0.0/0", "port": "443"}]})
        commands = [item[0] for item in calls]
        self.assertIn(["iptables", "-A", host_admin.FIREWALL_CHAIN, "-p", "tcp", "--dport", "22", "-j", "ACCEPT"], commands)
        self.assertIn((["iptables-restore"], "original"), calls)

    def test_protected_application_cannot_be_stopped_or_removed(self):
        for action in ("stop", "uninstall"):
            with self.assertRaises(ValueError):
                host_admin.application_action("docker", action)

    def test_unknown_application_never_reaches_subprocess(self):
        with mock.patch.object(host_admin, "_run") as run, self.assertRaises(ValueError):
            host_admin.application_action("nginx; reboot", "install")
        run.assert_not_called()


if __name__ == "__main__":
    unittest.main()
