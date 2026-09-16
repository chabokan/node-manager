"""Small, allow-listed host administration primitives used by the root worker.

No value received from the Hub is ever interpolated into a shell command.
"""

import ipaddress
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path


DNS_STATE = Path("/etc/chabokan-manager/nameservers.json")
RESOLV_CONF = Path("/etc/resolv.conf")
FIREWALL_STATE = Path("/etc/chabokan-manager/firewall.json")
FIREWALL_CHAIN = "CHABOKAN-INPUT"

APPLICATIONS = {
    "nginx": {"title": "Nginx", "package": "nginx", "binary": "nginx", "service": "nginx"},
    "apache": {"title": "Apache", "package": "apache2", "binary": "apache2", "service": "apache2"},
    "docker": {"title": "Docker", "package": "docker-ce", "binary": "docker", "service": "docker",
               "protected": True},
    "redis": {"title": "Redis", "package": "redis-server", "binary": "redis-server", "service": "redis-server"},
    "fail2ban": {"title": "Fail2ban", "package": "fail2ban", "binary": "fail2ban-client", "service": "fail2ban"},
    "certbot": {"title": "Certbot", "package": "certbot", "binary": "certbot"},
    "git": {"title": "Git", "package": "git", "binary": "git"},
    "curl": {"title": "cURL", "package": "curl", "binary": "curl"},
    "htop": {"title": "htop", "package": "htop", "binary": "htop"},
}


def _run(command, timeout=30, check=True, input_text=None):
    result = subprocess.run(command, capture_output=True, text=True, timeout=timeout,
                            check=False, input=input_text, env={**os.environ, "LC_ALL": "C"})
    if check and result.returncode:
        message = (result.stderr or result.stdout or "command failed").strip()
        raise RuntimeError(message[:500])
    return result


def _atomic_write(path, content, mode=0o600):
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}-", dir=path.parent)
    try:
        with os.fdopen(descriptor, "w") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.chmod(temporary, mode)
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _validate_nameservers(values):
    if not isinstance(values, list) or not 1 <= len(values) <= 3:
        raise ValueError("One to three nameservers are required")
    result = []
    for value in values:
        try:
            address = ipaddress.ip_address(str(value).strip())
        except ValueError as exc:
            raise ValueError("Invalid nameserver address") from exc
        if address.is_unspecified or address.is_multicast or address.is_link_local:
            raise ValueError("Unsafe nameserver address")
        normalized = str(address)
        if normalized not in result:
            result.append(normalized)
    if len(result) != len(values):
        raise ValueError("Duplicate nameservers are not allowed")
    return result


def set_nameservers(values):
    nameservers = _validate_nameservers(values)
    # Preserve search/options/comments and replace only resolver addresses.
    try:
        previous = RESOLV_CONF.read_text(errors="replace")
    except OSError as exc:
        raise RuntimeError("/etc/resolv.conf is not writable") from exc
    retained = [line for line in previous.splitlines()
                if not line.strip().startswith("nameserver ") and
                line.strip() != "# Managed by Chabokan server assistant"]
    content = "# Managed by Chabokan server assistant\n{}\n{}\n".format(
        "\n".join(f"nameserver {address}" for address in nameservers),
        "\n".join(retained)).rstrip() + "\n"
    try:
        _atomic_write(RESOLV_CONF, content, 0o644)
    except Exception:
        _atomic_write(RESOLV_CONF, previous, 0o644)
        raise
    _atomic_write(DNS_STATE, json.dumps(nameservers, separators=(",", ":")))
    return nameservers


def get_nameservers():
    managed = []
    try:
        values = json.loads(DNS_STATE.read_text())
        if isinstance(values, list):
            managed = _validate_nameservers(values)
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        pass
    active = []
    try:
        for line in RESOLV_CONF.read_text(errors="replace").splitlines():
            parts = line.split()
            if len(parts) == 2 and parts[0] == "nameserver":
                active.append(parts[1][:45])
    except OSError:
        pass
    return {"managed": managed[:3], "active": active[:5]}


def _normalize_firewall_rule(rule):
    if not isinstance(rule, dict):
        raise ValueError("Invalid firewall rule")
    action = str(rule.get("action", "accept")).lower()
    protocol = str(rule.get("protocol", "all")).lower()
    if action not in ("accept", "drop") or protocol not in ("all", "tcp", "udp", "icmp"):
        raise ValueError("Invalid firewall action or protocol")
    source = str(rule.get("source") or "0.0.0.0/0").strip()
    try:
        network = ipaddress.ip_network(source, strict=False)
        if network.version != 4:
            raise ValueError("Only IPv4 is supported by iptables")
        source = str(network)
    except ValueError as exc:
        raise ValueError("Invalid firewall source") from exc
    port = str(rule.get("port") or "").strip()
    if port:
        if protocol not in ("tcp", "udp"):
            raise ValueError("Ports require TCP or UDP")
        parts = port.replace("-", ":").split(":")
        if len(parts) not in (1, 2) or any(not part.isdigit() for part in parts):
            raise ValueError("Invalid port or port range")
        numbers = [int(part) for part in parts]
        if any(number < 1 or number > 65535 for number in numbers) or numbers != sorted(numbers):
            raise ValueError("Invalid port or port range")
        port = ":".join(str(number) for number in numbers)
    description = str(rule.get("description") or "").strip()[:100]
    return {"action": action, "protocol": protocol, "source": source,
            "port": port, "description": description}


def validate_firewall_config(config):
    if not isinstance(config, dict):
        raise ValueError("Invalid firewall configuration")
    enabled = config.get("enabled", True)
    default = str(config.get("default", "allow")).lower()
    rules = config.get("rules", [])
    if not isinstance(enabled, bool) or default not in ("allow", "deny"):
        raise ValueError("Invalid firewall configuration")
    if not isinstance(rules, list) or len(rules) > 100:
        raise ValueError("Too many firewall rules")
    return {"enabled": enabled, "default": default,
            "rules": [_normalize_firewall_rule(rule) for rule in rules]}


def _iptables_rule(rule):
    command = ["iptables", "-A", FIREWALL_CHAIN]
    if rule["protocol"] != "all":
        command += ["-p", rule["protocol"]]
    command += ["-s", rule["source"]]
    if rule["port"]:
        command += ["--dport", rule["port"]]
    command += ["-j", rule["action"].upper()]
    return command


def _ssh_ports():
    binary = shutil.which("sshd")
    if not binary and Path("/usr/sbin/sshd").exists():
        binary = "/usr/sbin/sshd"
    if not binary:
        return [22]
    result = _run([binary, "-T"], timeout=10, check=False)
    ports = []
    for line in result.stdout.splitlines():
        parts = line.split()
        if len(parts) == 2 and parts[0].lower() == "port" and parts[1].isdigit():
            port = int(parts[1])
            if 0 < port <= 65535 and port not in ports:
                ports.append(port)
    return ports or [22]


def set_firewall(config):
    config = validate_firewall_config(config)
    if not shutil.which("iptables"):
        raise RuntimeError("iptables is not installed")
    backup = _run(["iptables-save"]).stdout
    try:
        if _run(["iptables", "-nL", FIREWALL_CHAIN], check=False).returncode:
            _run(["iptables", "-N", FIREWALL_CHAIN])
        _run(["iptables", "-F", FIREWALL_CHAIN])
        if _run(["iptables", "-C", "INPUT", "-j", FIREWALL_CHAIN], check=False).returncode:
            _run(["iptables", "-I", "INPUT", "1", "-j", FIREWALL_CHAIN])
        # Safety rails: never cut established traffic, loopback, or SSH access.
        _run(["iptables", "-A", FIREWALL_CHAIN, "-m", "conntrack", "--ctstate",
              "ESTABLISHED,RELATED", "-j", "ACCEPT"])
        _run(["iptables", "-A", FIREWALL_CHAIN, "-i", "lo", "-j", "ACCEPT"])
        for ssh_port in _ssh_ports():
            _run(["iptables", "-A", FIREWALL_CHAIN, "-p", "tcp", "--dport",
                  str(ssh_port), "-j", "ACCEPT"])
        if config["enabled"]:
            for rule in config["rules"]:
                _run(_iptables_rule(rule))
            _run(["iptables", "-A", FIREWALL_CHAIN, "-j",
                  "RETURN" if config["default"] == "allow" else "DROP"])
        else:
            _run(["iptables", "-A", FIREWALL_CHAIN, "-j", "RETURN"])
    except Exception:
        _run(["iptables-restore"], input_text=backup, check=False)
        raise
    _atomic_write(FIREWALL_STATE, json.dumps(config, separators=(",", ":")))
    if shutil.which("netfilter-persistent"):
        _run(["netfilter-persistent", "save"], timeout=60)
    elif Path("/etc/iptables").exists():
        _atomic_write(Path("/etc/iptables/rules.v4"), _run(["iptables-save"]).stdout)
    return config


def get_firewall():
    try:
        config = validate_firewall_config(json.loads(FIREWALL_STATE.read_text()))
    except (OSError, ValueError, TypeError, json.JSONDecodeError):
        config = {"enabled": False, "default": "allow", "rules": []}
    config["available"] = bool(shutil.which("iptables"))
    return config


def application_inventory(detected_versions=None):
    detected_versions = detected_versions or {}
    packages = [app["package"] for app in APPLICATIONS.values()]
    package_versions = {}
    if shutil.which("dpkg-query"):
        result = _run(["dpkg-query", "-W", "-f=${Package}\t${Version}\n", *packages],
                      timeout=10, check=False)
        for line in result.stdout.splitlines():
            parts = line.split("\t", 1)
            if len(parts) == 2:
                package_versions[parts[0].split(":", 1)[0]] = parts[1][:120]
    services = [app["service"] for app in APPLICATIONS.values() if app.get("service")]
    service_states = {}
    if services and shutil.which("systemctl"):
        result = _run(["systemctl", "is-active", *services], timeout=10, check=False)
        service_states = dict(zip(services, result.stdout.splitlines()))
    rows = []
    for key, app in APPLICATIONS.items():
        binary = shutil.which(app["binary"])
        installed = bool(binary)
        version = detected_versions.get(app["title"], "")
        if not version:
            version = package_versions.get(app["package"], "")
        service = app.get("service")
        active = installed and bool(service) and service_states.get(service) == "active"
        rows.append({"key": key, "title": app["title"], "installed": installed,
                     "version": version, "service": bool(service), "active": active,
                     "protected": bool(app.get("protected"))})
    return rows


def application_action(key, action):
    app = APPLICATIONS.get(str(key))
    if not app or action not in ("install", "uninstall", "start", "stop", "reload"):
        raise ValueError("Unsupported application action")
    if app.get("protected") and action in ("uninstall", "stop"):
        raise ValueError("This application is required by the server assistant")
    if action == "install":
        _run(["apt-get", "install", "-y", "--no-install-recommends", app["package"]], timeout=600)
    elif action == "uninstall":
        _run(["apt-get", "remove", "-y", app["package"]], timeout=600)
    else:
        service = app.get("service")
        if not service:
            raise ValueError("This application has no system service")
        systemd_action = "reload-or-restart" if action == "reload" else action
        _run(["systemctl", systemd_action, service], timeout=60)
    return True


def execute_host_admin_job(name, data):
    if name == "server_nameservers_set":
        return set_nameservers(data.get("nameservers"))
    if name == "server_firewall_set":
        return set_firewall(data)
    if name == "server_application_action":
        return application_action(data.get("application"), data.get("action"))
    raise ValueError("Unsupported host administration job")
