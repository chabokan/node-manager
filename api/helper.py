import subprocess

import psutil


def get_system_info():
    system_info = {}

    # Getting CPU information
    cpu_info = {}
    cpu_info['count'] = psutil.cpu_count(logical=False)  # Physical CPU cores
    cpu_info['usage'] = psutil.cpu_percent(interval=1)  # CPU usage percentage
    system_info['cpu'] = cpu_info

    # Getting RAM information
    ram_info = {}
    ram = psutil.virtual_memory()
    ram_info['total'] = ram.total
    ram_info['available'] = ram.available
    ram_info['used'] = ram.used
    ram_info['free'] = ram.free
    ram_info['percent'] = ram.percent
    system_info['ram'] = ram_info

    # Getting disk information
    disk_info = {}
    disks = psutil.disk_partitions()
    for disk in disks:
        disk_info[disk.device] = {
            'total': psutil.disk_usage(disk.mountpoint).total,
            'used': psutil.disk_usage(disk.mountpoint).used,
            'free': psutil.disk_usage(disk.mountpoint).free,
            'percent': psutil.disk_usage(disk.mountpoint).percent
        }
    system_info['disk'] = disk_info

    return system_info


def run_bash_command(command):
    try:
        result = subprocess.check_output(command, shell=True, text=True)
        return result.strip()
    except subprocess.CalledProcessError as e:
        return f"Error: {e}"
