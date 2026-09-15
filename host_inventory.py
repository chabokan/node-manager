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


def collect_host_inventory():
    docker_ok, docker_output = _run_checked(
        ["docker", "ps", "-a", "--format", "{{json .}}"], timeout=8)
    containers, published = _docker_containers(docker_output if docker_ok else "")
    ss_ok, ss_output = _run_checked(["ss", "-H", "-lntu"])
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
