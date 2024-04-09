import json
import os
import string
import subprocess

import dateutil
import docker
import psutil
import pytz
import requests
from docker.errors import APIError
from datetime import datetime

import crud
from core.config import settings
from core.db import get_db


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
    return round(value / 1024 / 1024 / 1024, 2)


def set_job_run_in_hub(db, key):
    data = {
        "token": crud.get_setting(db, "token").value,
        "key": key,
        "status": "success"
    }
    headers = {
        "Content-Type": "application/json",
    }
    r = requests.post("https://hub.chabokan.net/fa/api/v1/servers/receive-server-jobs/", headers=headers,
                      data=json.dumps(data), timeout=15)


def get_home_path(data):
    platform = data['platform']
    home_path = f"/home/{data['name']}"
    if platform['name'].split(":")[0] in settings.STORAGE_PLATFORMS:
        home_path = f"/storage/{data['name']}"

    return home_path


def create_service(db, key, data):
    create_container_task(data)
    set_job_run_in_hub(db, key)


def delete_service(db, key, data):
    delete_container_task(data['name'])
    set_job_run_in_hub(db, key)


def change_user_home_path(username, home_path):
    os.system("usermod -d {home_path} {username}".format(username=username, home_path=home_path))
    os.system("chown {username}:{username} {home_path}".format(username=username, home_path=home_path))


def set_password(user, password):
    try:
        cmd = f"bash -c \"echo -e '{password}\\n{password}' | passwd {user}\""
        subprocess.check_call(cmd, shell=True)
    except:
        pass


def create_os_user(username, home_path, password):
    os.system("useradd -m {username} -s /bin/bash".format(username=username))
    change_user_home_path(username, home_path)
    set_password(username, str(password))


def get_volumes(main_volumes, name, container_options, home_path):
    volumes = []
    user_dir = home_path + "/"
    docker_manager = docker.from_env()
    for volume in main_volumes:
        vol_name = name + "_" + volume['name']
        key = user_dir + volume['name']
        value = volume['directory']
        if not os.path.exists(key):
            os.mkdir(key)
        try:
            docker_vol = docker_manager.volumes.get(vol_name)
            if docker_manager.volumes.get(vol_name).attrs['Options']['device'] != key:
                docker_manager.volumes.get(vol_name).remove()
                raise Exception("error")
        except:
            docker_manager.volumes.create(name=vol_name,
                                          driver="local",
                                          driver_opts={"device": key, "type": "none", "o": "bind"})

        if volume['type'] == "volume":
            volumes.append(f"{vol_name}:{value}")
        else:
            volumes.append(f"{key}:{value}")

        # if container and container.created > (timezone.now() - timedelta(hours=1)):
        #     if volume.type == "volume":
        #         volumes.append(f"{vol_name}:{value}")
        #     else:
        #         volumes.append(f"{key}:{value}")
        # else:
        #     volumes.append(f"{key}:{value}")

    if "volumes" in container_options:
        for volume in container_options['volumes']:
            # volume variable have slash in beginning
            final_vol = f"{home_path}{volume}"
            volumes.append(final_vol)

    return volumes


def get_container_default_image_name(platform, options):
    image = platform['image']
    container_options = options
    build_args = []
    if "build_args" in container_options:
        build_args = container_options['build_args']

    (image_repo, image_tag) = image.split(':')
    if len(build_args) > 0:
        image_tag = ""
        build_args.sort(key=lambda x: x["key"], reverse=False)
        for args in build_args:
            if len(image_tag) > 0:
                image_tag += "-" + args['value']
            else:
                image_tag += args['value']

    return image_repo, image_tag


def container_run(image, name, envs, ports, volumes, ram, cpu, platform_command=None, platform_name=None, limit=False,
                  home_path=None):
    run_response = {}
    device_name = os.popen('df -k /').read().splitlines()[1].split()[0]
    command = f"docker run -d "
    run_command = ""
    if platform_name and isinstance(platform_name, str) and "mysql" in platform_name:
        command += "--cap-add=sys_nice "

    for env in envs:
        if "CHBK_RUN_CMD" in env:
            run_command = env.split("=")[1]
        else:
            command += f"-e '{env}' "

    for volume in volumes:
        command += f"-v {volume} "

    if "NO_D_N_S" not in str(envs):
        command += "-v /etc/hosts:/etc/h_hosts:ro -v /etc/resolv.conf:/etc/resolv.conf:ro "

    for port in ports:
        command += f"-p {port}/tcp -p {port}/udp "

    cpu_count = os.cpu_count()
    db = next(get_db())
    all_usage = crud.get_all_server_usages(db)
    all_ram = all_usage[0].ram
    all_cpu = all_usage[0].cpu
    if limit:
        command += f'-m {ram}g --cpus="{cpu}" '
    else:
        command += f'-m {all_ram}g --cpus="{all_cpu}" '

    final_command = f'{command} --name {name} {image}'

    if platform_command and len(platform_command) > 1:
        final_command += f" {platform_command}"
    elif run_command and len(run_command) > 1:
        final_command += f" {run_command}"

    response = subprocess.getoutput(final_command)
    os.system(f"echo '{final_command}'")
    os.system(f"echo '{response}'")

    if "docker" in platform_name:
        os.system(f"chmod 777 -R {home_path}")

    run_response['response'] = 0
    run_response['final_command'] = final_command

    return run_response


def create_container_task(data):
    if data['platform']['name'] == "docker":
        pass
        # create_dockerfile_container_task(envs, platform)
    else:
        container_options = data['options']
        main_container_name = data['name']
        os.system(f"mkdir -p {get_home_path(data)}")

        create_os_user(main_container_name, get_home_path(data), container_options['ftp_password'])

        volumes = get_volumes(data['volumes'], main_container_name, container_options, get_home_path(data))
        container_ports = []
        for port in data['ports']:
            container_ports.append(f"{port['outside_port']}:{port['inside_port']}")

        uid = os.popen(f'id -u {main_container_name}').read()
        uid = int(uid)
        container_envs = data['envs']
        container_envs.append(f"CHBK_AS_USER={main_container_name}")
        container_envs.append(f"CHBK_USER_UID={uid}")
        image_repo, image_tag = get_container_default_image_name(data['platform'], data['options'])
        docker_manager = docker.from_env()
        try:
            docker_manager.images.pull(repository=image_repo, tag=image_tag)
        except:
            pass
        try:
            docker_manager.images.get(f"{image_repo}:{image_tag}")
        except:
            raise Exception("image not found")

        run_response = container_run(f"{image_repo}:{image_tag}", main_container_name, container_envs, container_ports,
                                     volumes,
                                     data['ram_limit'], data['cpu_limit'], data['platform']['command'],
                                     data['platform']['name'], home_path=get_home_path(data))

        if run_response['response'] != 0 and run_response['response'] != 32000:
            raise Exception("some problem in docker run command")

    #     tasks.limit_container(container.id, schedule=container.platform.build_time * 60)
    #
    # tasks.rebuild_firewall_rules(container.name)


# def create_dockerfile_container_task(container_id):
#     containers = Container.objects.filter(id=container_id)
#     if containers.count() > 0:
#         container = containers.first()
#         container_options = container.options
#         helper.create_os_user(container.name, container.home_path, container_options['ftp_password'])
#         volumes = helper.get_volumes(container.platform, container.name, container_options, get_home_path(container),
#                                      container)
#         ports = []
#         if container.ports and isinstance(container.ports, list):
#             for port in container.ports:
#                 ports.append(f"{port['outside_port']}:{port['inside_port']}")
#
#         envs = container.envs
#         try:
#             uid = os.popen(f'id -u {container.name}').read()
#             uid = int(uid)
#         except:
#             helper.create_os_user(container.name, container.home_path, container_options['ftp_password'])
#             uid = os.popen(f'id -u {container.name}').read()
#             uid = int(uid)
#
#         envs.append(f"CHBK_AS_USER={container.name}")
#         envs.append(f"CHBK_USER_UID={uid}")
#         home_path = get_home_path(container)
#         if os.path.exists(f"{home_path}/Dockerfile"):
#             helper.build_dockerfile(container.name)
#             run_response = helper.container_run(container.name, container.name, envs, ports, volumes,
#                                                 container.ram_limit,
#                                                 container.cpu_limit, platform_name=container.platform.name,
#                                                 home_path=container.home_path)
#             if run_response['response'] == 0:
#                 conti = Container.objects.get(name=container.name)
#                 conti.status = "building"
#                 conti.save()
#                 tasks.limit_container(container.id)


def delete_os_user(username, delete_home):
    command = "userdel "
    if delete_home:
        os.system(f"rm -rf /home/{username}")
        os.system(f"rm -rf /storage/{username}")

    os.system(f"{command}{username}")


def delete_volumes(container_name):
    docker_manager = docker.from_env()
    all_volumes = docker_manager.volumes.list()

    for volume in all_volumes:
        if volume.name.startswith(container_name):
            volume.remove()


def delete_container_task(container_name, delete_home=True, delete_image=True):
    delete_os_user(container_name, delete_home)
    docker_manager = docker.from_env()
    try:
        con = docker_manager.containers.get(container_name)
        con.remove(v=True, force=True)
    except:
        pass

    try:
        if delete_image:
            docker_manager.images.remove(container_name, force=True)
    except:
        pass

    try:
        delete_volumes(container_name)
    except:
        pass


def get_pass(password_len=12):
    new_password = None
    chars = string.ascii_lowercase + string.ascii_uppercase + string.digits

    while new_password is None or new_password[0] in string.digits:
        new_password = ''.join([chars[ord(os.urandom(1)) % len(chars)] for i in range(password_len)])
    return new_password


def change_ftp_password_task(container_name):
    set_password(container_name, str(get_pass()))


def stop_container_task(container_name):
    docker_manager = docker.from_env()
    container_exist = False
    docker_container = ""
    try:
        docker_container = docker_manager.containers.get(container_name)
        container_exist = True
    except:
        pass

    if container_exist:
        try:
            docker_container.stop()
        except:
            pass
            # check it


def update_container(data):
    container_name = data['name']
    home_path = get_home_path(data)
    if data['platform']['name'] == "docker":
        pass
    # update_dockerfile_container(container_name)
    else:
        container_ports = []
        for port in data['ports']:
            container_ports.append(f"{port['outside_port']}:{port['inside_port']}")

        try:
            uid = os.popen(f'id -u {container_name}').read()
            uid = int(uid)
            change_user_home_path(container_name, home_path)
        except:
            create_os_user(container_name, home_path, data['options']['ftp_password'])
            uid = os.popen(f'id -u {container_name}').read()
            uid = int(uid)

        container_envs = data['envs']
        container_envs.append(f"CHBK_AS_USER={container_name}")
        container_envs.append(f"CHBK_USER_UID={uid}")

        set_password(container_name, data['options']['ftp_password'])
        docker_manager = docker.from_env()
        container_exist = False
        docker_container = ""
        try:
            docker_container = docker_manager.containers.get(container_name)
            container_exist = True
        except:
            pass

        if container_exist:
            image_repo, image_tag = get_container_default_image_name(data['platform'], data['options'])
            try:
                docker_container.commit(repository=container_name, tag='latest')
                image_repo = container_name
                image_tag = "latest"
            except:
                pass
            try:
                docker_container.remove(v=True, force=True)
            except:
                pass
            volumes = get_volumes(data['volumes'], container_name, data['options'], home_path)
            run_response = container_run(f"{image_repo}:{image_tag}", container_name, container_envs, container_ports,
                                         volumes,
                                         data['ram_limit'],
                                         data['cpu_limit'], data['platform']['command'], data['platform']['name'],
                                         home_path=home_path)
            if run_response['response'] != 0 and run_response['response'] != 32000:
                raise Exception("some problem in docker run command")
            # tasks.limit_container(container.id)
        else:
            create_container_task(data)

    # tasks.rebuild_firewall_rules(container.name)


def pull_container_image(platform, container_options):
    if platform['name'] != "docker":
        build_args = []
        if "build_args" in container_options:
            build_args = container_options['build_args']

        image = platform['image']

        (image_repo, image_tag) = image.split(':')
        if len(build_args) > 0:
            image_tag = ""
            build_args.sort(key=lambda x: x["key"], reverse=False)
            for args in build_args:
                if len(image_tag) > 0:
                    image_tag += "-" + args['value']
                else:
                    image_tag += args['value']

        docker_manager = docker.from_env()
        try:
            docker_manager.images.pull(repository=image_repo, tag=image_tag)
        except:
            pass
        try:
            docker_manager.images.get(f"{image_repo}:{image_tag}")
        except:
            raise Exception("image not found")


def rebuild_container(data):
    try:
        pull_container_image(data['platform'], data['options'])
    except APIError:
        rebuild_container(data)

    if data['platform']['name'] == "docker":
        delete_container_task(data['name'], False, False)
    else:
        delete_container_task(data['name'], False, True)

    create_container_task(data)


def service_action(db, key, data):
    if data['action'] == "stop":
        stop_container_task(data['name'])
        change_ftp_password_task(data['name'])
        set_job_run_in_hub(db, key)
    elif data['action'] == "start":
        update_container(data)
        set_job_run_in_hub(db, key)
    elif data['action'] == "restart":
        update_container(data)
        set_job_run_in_hub(db, key)
    elif data['action'] == "rebuild":
        rebuild_container(data)
        set_job_run_in_hub(db, key)


def service_logs(data):
    docker_manager = docker.from_env()
    docker_container = docker_manager.containers.get(data['name'])
    final_logs = ""
    logs = str(docker_container.logs(timestamps=True, tail=1000).decode("utf-8"))
    for log_line in logs.splitlines():
        if log_line.startswith("2022") or log_line.startswith("2023") or log_line.startswith("2024"):
            log_date = log_line[:30]
            local_date_timestamp = dateutil.parser.isoparse(log_date).timestamp()
            zone_ir = pytz.timezone("Asia/Tehran")
            local_date = zone_ir.localize(datetime.fromtimestamp(local_date_timestamp)).strftime(
                "[%Y-%m-%d %H:%M:%S]")
            log_line = local_date + log_line[30:]

        final_logs += log_line
        final_logs += "\n"
