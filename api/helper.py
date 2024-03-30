import subprocess

import psutil
import requests


def get_system_info():
    system_info = {}

    # Getting CPU information
    cpu_info = {}
    cpu_info['count'] = psutil.cpu_count(logical=False)  # Physical CPU cores
    cpu_info['usage'] = round(psutil.cpu_percent(interval=1) / 100, 2)  # CPU usage percentage
    system_info['cpu'] = cpu_info

    # Getting RAM information
    ram_info = {}
    ram = psutil.virtual_memory()
    ram_info['total'] = byte_to_gb(ram.total)
    ram_info['available'] = byte_to_gb(ram.available)
    ram_info['used'] = byte_to_gb(ram.used)
    ram_info['free'] = byte_to_gb(ram.free)
    ram_info['percent'] = ram.percent
    system_info['ram'] = ram_info

    # Getting disk information
    disk_info = {}
    disks = psutil.disk_partitions()
    system_info['all_disk_space'] = 0
    system_info['all_disk_usage'] = 0
    for disk in disks:
        total = byte_to_gb(psutil.disk_usage(disk.mountpoint).total)
        used = byte_to_gb(psutil.disk_usage(disk.mountpoint).used)
        disk_info[disk.device] = {
            'total': total,
            'used': used,
            'free': byte_to_gb(psutil.disk_usage(disk.mountpoint).free),
            'percent': psutil.disk_usage(disk.mountpoint).percent
        }
    system_info['disk'] = disk_info
    for key, value in disk_info.items():
        system_info['all_disk_space'] += value['total']
        system_info['all_disk_usage'] += value['used']

    return system_info


def run_bash_command(command):
    try:
        result = subprocess.check_output(command, shell=True, text=True)
        return result.strip()
    except subprocess.CalledProcessError as e:
        return f"Error: {e}"


def get_server_ip():
    try:
        ip = requests.get("https://chabokan.net/ip/", timeout=15).json()['ip_address']
    except:
        ip = requests.get("https://shecan.ir/ip/", timeout=15).content.decode("utf-8")

    return ip


def byte_to_gb(value):
    return round(value / 1000 / 1000 / 1000, 2)
