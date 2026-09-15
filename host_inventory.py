"""Read-only host inventory collected by the host cron worker.

The web container has a different network namespace, so it must not inspect its
own listening sockets and present them as host ports.
"""

import json
import os
import platform
import re
import subprocess
import tempfile
from datetime import datetime, timezone
from pathlib import Path


INVENTORY_PATH = Path(__file__).resolve().parent / ".host-inventory.json"
VERSION_COMMANDS = {
    "Docker Engine": ["docker", "version", "--format", "{{.Server.Version}}"],
    "Docker Compose": ["docker", "compose", "version", "--short"],
    "Python": ["python3", "--version"],
    "Git": ["git", "--version"],
    "Nginx": ["nginx", "-v"],
    "OpenSSH": ["sshd", "-V"],
    "Node.js": ["node", "--version"],
    "npm": ["npm", "--version"],
    "PHP": ["php", "-v"],
    "MySQL client": ["mysql", "--version"],
    "PostgreSQL client": ["psql", "--version"],
}


def _run_checked(command, timeout=4):
    try:
        result = subprocess.run(command, capture_output=True, text=True,
                                timeout=timeout, check=False)
    except (OSError, subprocess.TimeoutExpired):
        return False, ""
    return result.returncode == 0, (result.stdout or result.stderr).strip()


def _run(command, timeout=4):
    success, output = _run_checked(command, timeout)
    return output if success else ""


def _listening_ports(output):
    ports = set()
    for line in output.splitlines():
        columns = line.split()
        if len(columns) < 5 or columns[0] not in ("tcp", "udp"):
            continue
        # ss -H -lntu: Netid State Recv-Q Send-Q Local Peer
        address = columns[4]
        match = re.search(r":(\d+)$", address)
        if not match:
            continue
        number = int(match.group(1))
        if 0 < number <= 65535:
            ports.add((columns[0], number, address[:match.start()]))
    return [{"protocol": protocol, "port": port, "address": address}
            for protocol, port, address in sorted(ports)[:200]]


def _docker_containers(output):
    containers = []
    published = set()
    for line in output.splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        name = str(row.get("Names", ""))[:250]
        state = str(row.get("State", ""))[:32]
        if name:
            containers.append({"name": name, "state": state})
        if state == "running":
            for address, port, protocol in re.findall(
                    r"((?:\[[^]]+\]|::|[\w.]+)):(\d+)->\d+/(tcp|udp)",
                    str(row.get("Ports", ""))):
                published.add((protocol, int(port), address, name))
    return containers[:500], [
        {"protocol": protocol, "port": port, "address": address, "container": name}
        for protocol, port, address, name in sorted(published)[:200]
    ]


def _host_disks(output):
    """Group mounted filesystems by their parent host block device."""
    try:
        devices = json.loads(output).get("blockdevices", [])
    except (ValueError, TypeError, AttributeError):
        return {}

    disks = {}
    for device in devices:
        if device.get("type") != "disk":
            continue
        path = device.get("path")
        if not path or not str(path).startswith("/dev/"):
            continue
        try:
            size = int(device.get("size") or 0)
        except (TypeError, ValueError):
            continue
        if size <= 0:
            continue
        mounts = []
        seen = set()
        counted_devices = set()
        used_bytes = 0

        def visit(node):
            nonlocal used_bytes
            for mount in node.get("mountpoints") or []:
                if not isinstance(mount, str) or not mount.startswith("/") or mount in seen:
                    continue
                seen.add(mount)
                try:
                    stats = os.statvfs(mount)
                except OSError:
                    continue
                mounts.append(mount)
                filesystem_device = node.get("path") or mount
                if filesystem_device not in counted_devices:
                    filesystem_size = stats.f_blocks * stats.f_frsize
                    used_bytes += max(filesystem_size - stats.f_bfree * stats.f_frsize, 0)
                    counted_devices.add(filesystem_device)
            for child in node.get("children") or []:
                visit(child)

        visit(device)
        gb = 1024 ** 3
        total = round(size / gb, 2)
        used = round(min(used_bytes, size) / gb, 2)
        disks[path] = {
            "device": path, "mountpoints": sorted(mounts),
            "total": total, "used": used,
            "free": round(max(size - used_bytes, 0) / gb, 2),
            "percent": round(min(used_bytes / size * 100, 100), 1),
        }
    return disks


def collect_host_inventory():
    docker_ok, docker_output = _run_checked(
        ["docker", "ps", "-a", "--format", "{{json .}}"], timeout=8)
    containers, published = _docker_containers(docker_output if docker_ok else "")
    ss_ok, ss_output = _run_checked(["ss", "-H", "-lntu"])
    disks_output = _run(["lsblk", "--json", "--bytes", "--output",
                         "PATH,TYPE,SIZE,MOUNTPOINTS"], timeout=8)
    versions = {}
    for name, command in VERSION_COMMANDS.items():
        if name == "OpenSSH" and Path("/usr/sbin/sshd").exists():
            command = ["/usr/sbin/sshd", "-V"]
        output = _run(command)
        if output:
            versions[name] = output.splitlines()[0][:120]
    versions["Kernel"] = platform.release()[:120]
    try:
        os_release = Path("/etc/os-release").read_text()
        match = re.search(r'^PRETTY_NAME="?([^"\n]+)', os_release, re.MULTILINE)
        if match:
            versions["Operating system"] = match.group(1)[:120]
    except OSError:
        pass
    return {
        "collected_at": datetime.now(timezone.utc).isoformat(),
        "listening_ports": _listening_ports(ss_output if ss_ok else ""),
        "listening_ports_available": ss_ok,
        "published_ports": published,
        "containers_available": docker_ok,
        "versions": versions,
        "containers": containers,
        "disks": _host_disks(disks_output),
    }


def write_host_inventory():
    inventory = collect_host_inventory()
    descriptor, temporary = tempfile.mkstemp(prefix=".host-inventory-",
                                              dir=INVENTORY_PATH.parent)
    try:
        with os.fdopen(descriptor, "w") as stream:
            json.dump(inventory, stream)
        os.chmod(temporary, 0o600)
        os.replace(temporary, INVENTORY_PATH)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def read_host_inventory(max_age_seconds=900):
    try:
        inventory = json.loads(INVENTORY_PATH.read_text())
        collected = datetime.fromisoformat(inventory["collected_at"])
        if (datetime.now(timezone.utc) - collected).total_seconds() > max_age_seconds:
            return None
        return inventory
    except (OSError, ValueError, KeyError, TypeError):
        return None
