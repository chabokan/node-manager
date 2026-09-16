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

    def test_openssh_is_reported_and_can_be_managed(self):
        self.assertIn("openssh", host_admin.APPLICATIONS)
        with mock.patch.object(host_admin, "_run") as run:
            host_admin.application_action("openssh", "stop")
        run.assert_called_once_with(["systemctl", "stop", "ssh"], timeout=60)

    def test_temporary_root_ftp_is_configured_and_scheduled(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            config = root / "vsftpd.conf"
            chroot = root / "vsftpd.chroot_list"
            ftpusers = root / "ftpusers"
            user_list = root / "user_list"
            state = root / "state.json"
            service_unit = root / "root-ftp.service"
            timer_unit = root / "root-ftp.timer"
            config.write_text("listen=YES\n#chroot_list_enable=NO\n")
            ftpusers.write_text("root\nbackup\n")
            user_list.write_text("root\n")
            with mock.patch.multiple(host_admin, VSFTPD_CONFIG=config,
                                     VSFTPD_CHROOT_LIST=chroot, FTPUSERS=ftpusers,
                                     VSFTPD_USER_LIST=user_list, ROOT_FTP_STATE=state,
                                     ROOT_FTP_SERVICE_UNIT=service_unit,
                                     ROOT_FTP_TIMER_UNIT=timer_unit), \
                 mock.patch.object(host_admin, "_run", return_value=mock.Mock(returncode=0)) as run:
                host_admin.enable_root_ftp()
            self.assertIn("chroot_list_enable=YES", config.read_text())
            self.assertIn(f"chroot_list_file={chroot}", config.read_text())
            self.assertIn("root", chroot.read_text().splitlines())
            self.assertNotIn("root", ftpusers.read_text().splitlines())
            self.assertIn("Persistent=true", timer_unit.read_text())
            self.assertTrue(any(call.args[0][:3] == ["systemctl", "enable", "--now"]
                                for call in run.call_args_list))

    def test_disabling_root_ftp_blocks_root_again(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            ftpusers = root / "ftpusers"
            user_list = root / "user_list"
            state = root / "state.json"
            ftpusers.write_text("backup\n")
            user_list.write_text("")
            with mock.patch.multiple(host_admin, FTPUSERS=ftpusers,
                                     VSFTPD_USER_LIST=user_list, ROOT_FTP_STATE=state), \
                 mock.patch.object(host_admin, "_run", return_value=mock.Mock(returncode=0)):
                host_admin.disable_root_ftp()
            self.assertIn("root", ftpusers.read_text().splitlines())
            self.assertIn("root", user_list.read_text().splitlines())

    def test_vsftpd_allowlist_mode_is_handled_without_inverting_access(self):
        config = "userlist_enable=YES\nuserlist_deny=NO\n"
        self.assertTrue(host_admin._config_bool(config, "userlist_enable", False))
        self.assertFalse(host_admin._config_bool(config, "userlist_deny", True))


if __name__ == "__main__":
    unittest.main()
