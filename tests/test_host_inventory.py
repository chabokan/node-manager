import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest import mock

import host_inventory


class HostInventoryTests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main()
