import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import host_inventory


class HostInventoryTests(unittest.TestCase):
    def test_old_lsblk_mountpoint_column_is_supported(self):
        output = json.dumps({'blockdevices': [
            {'path': '/dev/vda', 'type': 'disk', 'size': 100 * 1024 ** 3,
             'children': [{'path': '/dev/vda1', 'type': 'part',
                           'mountpoint': '/'}]}
        ]})
        stats = mock.Mock(f_blocks=100, f_bfree=60, f_bavail=60,
                          f_frsize=1024 ** 3)
        with mock.patch('host_inventory.os.statvfs', return_value=stats):
            disks = host_inventory._host_disks(output)
        self.assertEqual(disks['/dev/vda']['mountpoints'], ['/'])
        self.assertEqual(disks['/dev/vda']['used'], 40)

    def test_collect_retries_lsblk_without_mountpoints_column(self):
        output = json.dumps({'blockdevices': [
            {'path': '/dev/vda', 'type': 'disk', 'size': 100 * 1024 ** 3,
             'children': [{'path': '/dev/vda1', 'type': 'part',
                           'mountpoint': '/'}]}]})
        commands = []

        def run(command, timeout=4):
            commands.append(command)
            if command[:1] == ['lsblk'] and 'MOUNTPOINTS' not in command[-1]:
                return output
            return ''

        with mock.patch('host_inventory._run', side_effect=run), \
             mock.patch('host_inventory._run_checked', return_value=(False, '')), \
             mock.patch('host_inventory.os.statvfs', return_value=mock.Mock(
                 f_blocks=100, f_bfree=60, f_bavail=60,
                 f_frsize=1024 ** 3)):
            inventory = host_inventory.collect_host_inventory()
        self.assertIn('/dev/vda', inventory['disks'])
        self.assertEqual(inventory['disks']['/dev/vda']['used'], 40)
        self.assertEqual([command[-1] for command in commands
                          if command[:1] == ['lsblk']],
                         ['NAME,PATH,TYPE,SIZE,MOUNTPOINTS',
                          'NAME,PATH,TYPE,SIZE,MOUNTPOINT'])

    def test_disks_are_grouped_by_host_device_and_bind_mount_is_counted_once(self):
        output = json.dumps({'blockdevices': [
            {'path': '/dev/vda', 'type': 'disk', 'size': 100 * 1024 ** 3,
             'children': [{'path': '/dev/vda1', 'type': 'part',
                           'mountpoints': ['/', '/home']}]},
            {'path': '/dev/vdb', 'type': 'disk', 'size': 200 * 1024 ** 3,
             'children': [{'path': '/dev/vdb1', 'type': 'part',
                           'mountpoints': ['/storage']}]},
            {'path': '/dev/loop0', 'type': 'loop', 'size': 10 * 1024 ** 3,
             'mountpoints': ['/container']},
        ]})
        stats = mock.Mock(f_blocks=100, f_bfree=60, f_bavail=60,
                          f_frsize=1024 ** 3)
        with mock.patch('host_inventory.os.statvfs', return_value=stats):
            disks = host_inventory._host_disks(output)
        self.assertEqual(set(disks), {'/dev/vda', '/dev/vdb'})
        self.assertEqual(disks['/dev/vda']['mountpoints'], ['/', '/home'])
        self.assertEqual(disks['/dev/vda']['used'], 40)

    def test_disk_capacity_is_separate_from_filesystem_usage(self):
        output = json.dumps({'blockdevices': [
            {'path': '/dev/sda', 'type': 'disk', 'size': 52 * 1024 ** 3,
             'children': [{'path': '/dev/sda1', 'type': 'part',
                           'mountpoints': ['/']}]}
        ]})
        # Mirrors df: a 51 GiB filesystem with 11 GiB used and 40 GiB available.
        stats = mock.Mock(f_blocks=51, f_bfree=40, f_bavail=40,
                          f_frsize=1024 ** 3)
        with mock.patch('host_inventory.os.statvfs', return_value=stats):
            disk = host_inventory._host_disks(output)['/dev/sda']
        self.assertEqual((disk['capacity'], disk['total'], disk['used'],
                          disk['free'], disk['percent']), (52, 51, 11, 40, 21.6))
        self.assertTrue(disk['usage_available'])

    def test_inventory_without_mounted_filesystem_does_not_replace_good_data(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'inventory.json'
            path.write_text('{"good": true}')
            with mock.patch.object(host_inventory, 'INVENTORY_PATH', path), \
                 mock.patch.object(host_inventory, 'collect_host_inventory',
                                   return_value={'disks': {'/dev/sda': {
                                       'usage_available': False}}}):
                with self.assertRaises(OSError):
                    host_inventory.write_host_inventory()
            self.assertEqual(path.read_text(), '{"good": true}')

    def test_ss_parser_keeps_host_bind_addresses_and_protocols(self):
        output = (
            "tcp LISTEN 0 128 0.0.0.0:22 0.0.0.0:*\n"
            "tcp LISTEN 0 128 127.0.0.1:8123 0.0.0.0:*\n"
            "udp UNCONN 0 0 [::]:53 [::]:*\n"
        )
        self.assertEqual(host_inventory._listening_ports(output), [
            {"protocol": "tcp", "port": 22, "address": "0.0.0.0"},
            {"protocol": "tcp", "port": 8123, "address": "127.0.0.1"},
            {"protocol": "udp", "port": 53, "address": "[::]"},
        ])

    def test_stopped_container_ports_are_not_reported_as_published(self):
        output = "\n".join(json.dumps(row) for row in (
            {"Names": "app", "State": "running", "Ports": "0.0.0.0:443->443/tcp, :::443->443/tcp"},
            {"Names": "old", "State": "exited", "Ports": "0.0.0.0:8080->80/tcp"},
        ))
        containers, ports = host_inventory._docker_containers(output)
        self.assertEqual(len(containers), 2)
        self.assertEqual(ports, [
            {"protocol": "tcp", "port": 443, "address": "0.0.0.0", "container": "app"},
            {"protocol": "tcp", "port": 443, "address": "::", "container": "app"},
        ])

    def test_stale_inventory_is_not_read(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "inventory.json"
            path.write_text(json.dumps({"collected_at": (
                datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()}))
            with mock.patch.object(host_inventory, "INVENTORY_PATH", path):
                self.assertIsNone(host_inventory.read_host_inventory())

    def test_management_state_is_included_in_inventory(self):
        with mock.patch('host_inventory._run_checked', return_value=(False, '')), \
             mock.patch('host_inventory._run', return_value=''), \
             mock.patch('host_inventory.get_nameservers', return_value={'active': ['1.1.1.1'], 'managed': []}), \
             mock.patch('host_inventory.get_firewall', return_value={'enabled': False, 'rules': []}), \
             mock.patch('host_inventory.application_inventory', return_value=[{'key': 'nginx'}]), \
             mock.patch('host_inventory.collect_network_inventory', return_value={'available': True}):
            inventory = host_inventory.collect_host_inventory()
        self.assertEqual(inventory['nameservers']['active'], ['1.1.1.1'])
        self.assertEqual(inventory['applications'][0]['key'], 'nginx')

    def test_network_interfaces_and_routes_are_safely_normalized(self):
        interfaces = host_inventory._network_interfaces(json.dumps([{
            'ifname': 'eth0', 'operstate': 'UP', 'address': 'aa:bb:cc:dd:ee:ff',
            'mtu': 1500, 'addr_info': [
                {'family': 'inet', 'local': '192.0.2.10', 'prefixlen': 24, 'scope': 'global'},
                {'family': 'packet', 'local': 'ignored'}]}]))
        routes = host_inventory._network_routes(json.dumps([{
            'dst': 'default', 'gateway': '192.0.2.1', 'dev': 'eth0',
            'protocol': 'static', 'metric': 100}]))
        self.assertEqual(interfaces[0]['state'], 'up')
        self.assertEqual(interfaces[0]['addresses'][0]['address'], '192.0.2.10')
        self.assertEqual(routes[0]['gateway'], '192.0.2.1')

    def test_network_inventory_does_not_fail_on_invalid_numeric_fields(self):
        interfaces = host_inventory._network_interfaces(json.dumps([{
            'ifname': 'eth0', 'mtu': 'invalid', 'addr_info': [{
                'family': 'inet', 'local': '192.0.2.10', 'prefixlen': 'invalid'}]}]))
        routes = host_inventory._network_routes(json.dumps([{'metric': 'invalid'}]))
        self.assertEqual(interfaces[0]['mtu'], 0)
        self.assertEqual(interfaces[0]['addresses'][0]['prefix'], 0)
        self.assertEqual(routes[0]['metric'], 0)

    def test_network_probe_parses_latency_without_shell(self):
        with mock.patch('host_inventory._run_checked', return_value=(
                True, '64 bytes from 8.8.8.8: time=12.4 ms')) as run:
            result = host_inventory._network_probe(('Google DNS', '8.8.8.8'))
        self.assertTrue(result['reachable'])
        self.assertEqual(result['latency_ms'], 12.4)
        run.assert_called_once_with(
            ['ping', '-n', '-c', '1', '-W', '2', '8.8.8.8'], timeout=3)


if __name__ == "__main__":
    unittest.main()
