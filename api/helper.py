import json
import os
import shutil
import string
import subprocess
from random import randint
from time import sleep

import boto3
import dateutil
import docker
import psutil
import pymysql
import pytz
import requests
from docker.errors import APIError
from datetime import datetime, timedelta

import crud
from core.config import settings
from core.db import get_db
from models import ServiceUsage, ServerRootJob
from urllib.parse import unquote
import urllib.request


def get_system_info():
    system_info = {}

    # Getting CPU information
    cpu_info = {}
    cpu_info['count'] = psutil.cpu_count()
    cpu_info['usage'] = round((psutil.cpu_percent(interval=1) / 100) * cpu_info['count'], 2)
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


def set_job_run_in_hub(db, key, status="success"):
    data = {
        "token": crud.get_setting(db, "token").value,
        "key": key,
        "status": status
    }
    headers = {
        "Content-Type": "application/json",
    }
    base_hub_url = crud.get_setting(db, "base_hub_url").value

    r = requests.post(f"https://{base_hub_url}/fa/api/v1/servers/receive-server-jobs/", headers=headers,
                      data=json.dumps(data), timeout=45)


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
    server_info = get_system_info()
    all_ram = server_info['ram']['total']
    all_cpu = server_info['cpu']['count']
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


def normalize_permanent_path(path):
    new_path = path
    if path.startswith("/"):
        new_path = new_path[1:]

    if not new_path.startswith("/"):
        return new_path
    else:
        normalize_permanent_path(new_path)


def process_deploy_file_task(data):
    deploy_file_name = f'{data["name"]}{randint(111111, 999999)}.tar.gz'
    request = requests.get(data["url"], timeout=10, stream=True)
    with open(deploy_file_name, 'wb') as fh:
        for chunk in request.iter_content(1024 * 1024):
            fh.write(chunk)

    volumes = data['volumes']
    service_home_path = get_home_path(data)

    if "--d--" in data["name"]:
        name = data["name"].split("--d--")[0]
        home_path = get_home_path(data)
        main_service_home_path = home_path
        os.system(f"rm -rf '{service_home_path}'")
        os.system(f"cp -R '{main_service_home_path}/'"
                  f" '{service_home_path}/'")

    main_volume_path = service_home_path
    if len(volumes) > 0:
        main_vol = {}
        for vol in volumes:
            if vol['is_main']:
                main_vol = vol

        main_volume_path = f"{service_home_path}/{main_vol['name']}"

    if "options" in data:
        options = json.loads(data['deploy_options'])
        if "destination" in options:
            home_path = get_home_path(data)
            des = options['destination']
            if des.startswith("/"):
                main_volume_path = f"{home_path}{des}"
            else:
                main_volume_path = f"{home_path}/{des}"

    deploy_tmp_path = f"/tmp/{data['name']}-tmp-deploy/"
    try:
        # copy permanent paths files to tmp
        if "permanent_paths" in data:
            os.system(f"rm -rf '{deploy_tmp_path}' && mkdir -p '{deploy_tmp_path}'")
            for permanent_path in data['permanent_paths']:
                final_permanent_path = normalize_permanent_path(permanent_path['path'])
                final_permanent_path_directory = ""
                if len(final_permanent_path.split('/')) > 1:
                    final_permanent_path_directory = f"{final_permanent_path}".split('/')
                    final_permanent_path_directory.pop()
                    final_permanent_path_directory = '/'.join(final_permanent_path_directory)

                if os.path.exists(f"{main_volume_path}/{final_permanent_path}"):
                    os.system(f"mkdir -p '{deploy_tmp_path}/{final_permanent_path_directory}'")
                    os.system(f"mv '{main_volume_path}/{final_permanent_path}'"
                              f" '{deploy_tmp_path}/{final_permanent_path_directory}'")

        # remove and create main path
        os.system(f"rm -rf '{main_volume_path}'")
        os.mkdir(main_volume_path)

        # move deploy file and untar deploy to main path
        os.system(f"mv '{deploy_file_name}' '{main_volume_path}'")
        os.system(f"tar -xf {main_volume_path}/{deploy_file_name} -C {main_volume_path}")
        os.system(f"rm -rf '{main_volume_path}/{deploy_file_name}'")

        if "permanent_paths" in data:
            # copy permanent paths files from tmp to main path
            for permanent_path in data['permanent_paths']:
                # first we should remove permanent paths existed
                final_permanent_path = normalize_permanent_path(permanent_path['path'])
                if os.path.exists(f"{deploy_tmp_path}/{final_permanent_path}"):
                    os.system(f"rm -rf '{main_volume_path}/{final_permanent_path}'")

                    if len(final_permanent_path.split('/')) > 1:
                        final_permanent_path_directory = f"{final_permanent_path}".split('/')
                        final_permanent_path_directory.pop()
                        final_permanent_path_directory = '/'.join(final_permanent_path_directory)
                        os.system(f"mkdir -p '{main_volume_path}/{final_permanent_path_directory}'")

                    os.system(
                        f"mv '{deploy_tmp_path}/{final_permanent_path}' '{main_volume_path}/{final_permanent_path}'")
    finally:
        if os.path.exists(deploy_tmp_path):
            os.system(f"rm -rf {deploy_tmp_path}")

        if os.path.exists(deploy_file_name):
            os.system(f"rm -rf {deploy_file_name}")

    rebuild_container(data)


def process_zero_down_time_deploy(data):
    name = data['name'] + "--d--" + data['deploy_key']
    process_deploy_file_task(data)


def deploy_task(data):
    if data['zero_down_time']:
        process_zero_down_time_deploy(data)
    else:
        process_deploy_file_task(data)


def limit_container_task(data):
    command = "docker update "
    command += f'-m {data["ram_limit"]}g --cpus="{data["cpu_limit"]}" '
    command += f'{data["container_name"]}'
    os.system(command)


def str2bool(v):
    if isinstance(v, bool):
        return v
    return v.lower() in ("yes", "true", "t", "1")


def build_dockerfile(name, home_path):
    main_name = name
    if os.path.exists(f"{home_path}/Dockerfile"):
        os.system(f"rm -rf /builds/{main_name}")
        os.system(f"cp -rf {home_path} /builds/{main_name}")
        final_dockerfile = ""
        if os.path.exists(f"/builds/{main_name}/Dockerfile"):
            read_dockerfile = open(f"/builds/{main_name}/Dockerfile", "r")
            docker_files_lines = []
            from_line = 0
            for line in read_dockerfile.readlines():
                new_line = line
                if line.upper().startswith("FROM"):

                    split_line = line.split("/")
                    if len(split_line) > 1:
                        if ".net" not in split_line[0] and \
                                ".io" not in split_line[0] and \
                                ".org" not in split_line[0] and \
                                ".com" not in split_line[0]:
                            new_line = "FROM docker.chabokan.net/" + split_line[0][5:] + "/" + '/'.join(
                                split_line[1:])
                    else:
                        new_line = "FROM docker.chabokan.net/" + line[5:]

                docker_files_lines.append(new_line)

            for docker_files_line in docker_files_lines:
                final_dockerfile += docker_files_line

            read_dockerfile.close()
            try:
                write_dockerfile = open(f"/builds/{main_name}/Dockerfile", "w")
                write_dockerfile.write(final_dockerfile)
                write_dockerfile.close()

                build_logs = subprocess.getoutput(
                    f'timeout 30m docker build -m 8g --cpuset-cpus="0-3" /builds/{main_name} -t {main_name}')
                docker_manager = docker.from_env()
                build_note = ""
                try:
                    docker_manager.images.get(main_name)
                except:
                    build_note = "\n *** Dockerfile Build Failed, Please Review Your Dockerfile and Try again. *** \n\n"

            finally:
                shutil.rmtree(f"/builds/{main_name}", ignore_errors=True)


def create_container_task(data):
    if data['platform']['name'] == "docker":
        create_dockerfile_container_task(data)
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

        limit_container_job(main_container_name, data['ram_limit'], data['cpu_limit'])
    #
    # tasks.rebuild_firewall_rules(container.name)


def create_dockerfile_container_task(data):
    container_options = data['options']
    main_container_name = data['name']
    create_os_user(main_container_name, get_home_path(data), container_options['ftp_password'])
    volumes = get_volumes(data['volumes'], main_container_name, container_options, get_home_path(data))

    container_ports = []
    for port in data['ports']:
        container_ports.append(f"{port['outside_port']}:{port['inside_port']}")

    container_envs = data['envs']
    try:
        uid = os.popen(f'id -u {main_container_name}').read()
        uid = int(uid)
    except:
        create_os_user(main_container_name, get_home_path(data), container_options['ftp_password'])
        uid = os.popen(f'id -u {main_container_name}').read()
        uid = int(uid)

    container_envs.append(f"CHBK_AS_USER={main_container_name}")
    container_envs.append(f"CHBK_USER_UID={uid}")
    home_path = get_home_path(data)
    if os.path.exists(f"{home_path}/Dockerfile"):
        build_dockerfile(main_container_name, home_path)
        run_response = container_run(main_container_name, main_container_name, container_envs, container_ports, volumes,
                                     data['ram_limit'], data['cpu_limit'], platform_name=data['platform']['name'],
                                     home_path=get_home_path(data))
        if run_response['response'] == 0:
            limit_container_job(main_container_name, data['ram_limit'], data['cpu_limit'])


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


def update_dockerfile_container_task(data):
    container_name = data['name']
    home_path = get_home_path(data)
    container_options = data['options']
    container_envs = data['envs']
    volumes = get_volumes(data['volumes'], container_name, data['options'], home_path)
    container_ports = []
    for port in data['ports']:
        container_ports.append(f"{port['outside_port']}:{port['inside_port']}")

    set_password(container_name, container_options['ftp_password'])
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
            docker_container.remove(v=True, force=True)
        except:
            pass

    home_path = get_home_path(data)
    if os.path.exists(f"{home_path}/Dockerfile"):
        build_dockerfile(container_name, home_path)
        run_response = container_run(container_name, container_name, container_envs, container_ports, volumes,
                                     data['ram_limit'], data['cpu_limit'], platform_name=data['platform']['name'],
                                     home_path=home_path)
        if run_response['response'] == 0:
            limit_container_job(container_name, data['ram_limit'], data['cpu_limit'])


def update_container(data):
    container_name = data['name']
    home_path = get_home_path(data)
    if data['platform']['name'] == "docker":
        update_dockerfile_container_task(data)
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
            limit_container_job(container_name, data['ram_limit'], data['cpu_limit'])
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


def random_job_key(count):
    a_pass = get_pass(count)
    db = next(get_db())
    if crud.get_server_root_job(db, a_pass):
        return random_job_key(count)

    return a_pass


def limit_container_job(container_name, ram_limit, cpu_limit):
    db = next(get_db())
    data = {
        "container_name": container_name,
        "ram_limit": ram_limit,
        "cpu_limit": cpu_limit
    }
    crud.create_server_root_job(db, ServerRootJob(name="limit_container", key=random_job_key(64),
                                                  data=json.dumps(data),
                                                  run_at=datetime.now() + timedelta(seconds=300)))


def service_action(db, key, data):
    job_complete = False
    if data['action'] == "stop":
        stop_container_task(data['name'])
        change_ftp_password_task(data['name'])
        job_complete = True
    elif data['action'] == "start":
        update_container(data)
        job_complete = True
    elif data['action'] == "restart":
        update_container(data)
        job_complete = True
    elif data['action'] == "rebuild":
        rebuild_container(data)
        job_complete = True
    elif data['action'] == "silent-update":
        limit_container_job(data['name'], data['ram_limit'], data['cpu_limit'])
        job_complete = True

    return job_complete


def service_logs(name):
    final_logs = ""
    try:
        docker_manager = docker.from_env()
        docker_container = docker_manager.containers.get(name)
        logs = str(docker_container.logs(timestamps=True, tail=1000).decode("utf-8"))
        for log_line in logs.splitlines():
            if log_line.startswith("2022") or log_line.startswith("2023") or log_line.startswith(
                    "2024") or log_line.startswith("2025") or log_line.startswith("2026"):
                log_date = log_line[:30]
                local_date_timestamp = dateutil.parser.isoparse(log_date).timestamp()
                zone_ir = pytz.timezone("Asia/Tehran")
                local_date = zone_ir.localize(datetime.fromtimestamp(local_date_timestamp)).strftime(
                    "[%Y-%m-%d %H:%M:%S]")
                log_line = local_date + log_line[30:]

            final_logs += log_line
            final_logs += "\n"
    except:
        pass
    return final_logs


def usage_to_byte(usage):
    if "MiB" in usage or "MB" in usage:
        usage = usage.replace("MiB", "")
        usage = usage.replace("MB", "")
        usage = float(usage.strip())
        usage = usage * 1000 * 1000

    elif "GiB" in usage or "GB" in usage:
        usage = usage.replace("GiB", "")
        usage = usage.replace("GB", "")
        usage = float(usage.strip())
        usage = usage * 1000 * 1000 * 1000

    elif "TB" in usage:
        usage = usage.replace("TB", "")
        usage = float(usage.strip())
        usage = usage * 1000 * 1000 * 1000 * 1000

    elif "kB" in usage:
        usage = usage.replace("kB", "")
        usage = float(usage.strip())
        usage = usage * 1000

    elif "KiB" in usage:
        usage = usage.replace("KiB", "")
        usage = float(usage.strip())
        usage = usage * 1000
    else:
        usage = usage.replace("B", "")
        usage = float(usage.strip())
        usage = usage

    return usage


def normalize_cpu_usage(cpu_data):
    cpu_usage = cpu_data.replace("%", "")
    cpu_usage = round(float(cpu_usage) / 100, 1)

    return cpu_usage


def normalize_net_usage(net_data):
    network_rx = net_data.split("/")[0]
    network_tx = net_data.split("/")[1]

    network_rx = usage_to_byte(network_rx)
    network_tx = usage_to_byte(network_tx)

    return {"network_rx": network_rx, "network_tx": network_tx}


def normalize_block_usage(block_data):
    read = block_data.split("/")[0]
    write = block_data.split("/")[1]

    read = usage_to_byte(read)
    write = usage_to_byte(write)

    return {"read": read, "write": write}


def normalize_ram_usage(ram_data):
    ram_usage = ram_data.split("/")[0]
    ram_usage = usage_to_byte(ram_usage)
    return ram_usage


def get_service_size(path=os.getcwd()):
    main_path = f'/{path.split("/")[1]}/'
    final_size = 4
    try:
        all_dirs = subprocess.check_output(['duc', 'ls', main_path]).splitlines()
        dir_sizes = {}
        for dir_size in all_dirs:
            split_size_and_name = dir_size.split()
            dir_sizes[str(split_size_and_name[-1].decode("utf-8"))] = str(split_size_and_name[-2].decode("utf-8"))
        try:
            size = dir_sizes[path.replace('/home/', '').replace('/home2/', '').replace('/storage/', '')]
            if "G" in size:
                size = size.replace("G", '')
                final_size = float(size) * 1024 * 1024
            elif "M" in size:
                size = size.replace("M", '')
                final_size = float(size) * 1024
            else:
                size = size.replace("K", '')
                final_size = float(size)
        except:
            pass
    except:
        pass

    return float(final_size)


def get_container_disk_size(container_name):
    container_disk_size = os.popen('docker ps -s --filter "name=' + container_name + '" --format "{{.Size}}"').read()

    container_disk_size = container_disk_size.split(" ")[0]
    if "GB" in container_disk_size:
        container_disk_size = container_disk_size.replace("GB", "")
        container_disk_size = float(container_disk_size.strip())
        container_disk_size = container_disk_size * 1024 * 1024
    elif "MB" in container_disk_size:
        container_disk_size = container_disk_size.replace("MB", "")
        container_disk_size = float(container_disk_size.strip())
        container_disk_size = container_disk_size * 1024
    elif "kB" in container_disk_size:
        container_disk_size = container_disk_size.replace("kB", "")
        container_disk_size = float(container_disk_size.strip())
    else:
        container_disk_size = container_disk_size.replace("B", "")
        if container_disk_size:
            container_disk_size = float(container_disk_size.strip())
        else:
            container_disk_size = 0

        if container_disk_size != 0:
            container_disk_size = round(container_disk_size / 1024)

    container_disk_size = round(container_disk_size + get_service_size(f"/home/{container_name}"))
    container_disk_size += round(get_service_size(f"/home2/{container_name}"))
    container_disk_size += round(get_service_size(f"/storage/{container_name}"))

    return container_disk_size


def cal_all_containers_stats(db):
    data = []
    all_usages = os.popen(
        'docker stats --format "{{.Name}}: {{.MemUsage}}  --  {{.CPUPerc}} --  {{.NetIO}}  -- {{.BlockIO}}" --no-stream').read()
    containers_stats = all_usages.split("\n")

    if len(containers_stats) < 1:
        raise Exception("error in monitor services usage")
    else:
        for container_stat in containers_stats:
            container_name = container_stat.split(":")[0]
            if len(container_name) > 0:
                cpu_usage = normalize_cpu_usage(container_stat.split(":")[1].split("--")[1].strip())
                ram_usage = normalize_ram_usage(container_stat.split(":")[1].split("--")[0].strip())
                container_disk_size = get_container_disk_size(container_name)
                net_usage = normalize_net_usage(container_stat.split(":")[1].split("--")[2].strip())
                block_usage = normalize_block_usage(container_stat.split(":")[1].split("--")[3].strip())

                usage = ServiceUsage(name=container_name, ram=ram_usage, cpu=cpu_usage, read=block_usage['read'],
                                     write=block_usage['write'], network_rx=net_usage["network_rx"],
                                     network_tx=net_usage["network_tx"], disk=container_disk_size,
                                     created=datetime.now())
                data.append(usage)
        crud.create_bulk_service_usage(db, data)

    return data


def get_size(path=os.getcwd()):
    try:
        size = subprocess.check_output(['du', '-s', path], timeout=5).split()[0].decode('utf-8')
        if size == '0':
            size = '4'
    except:
        size = '3'
    return float(size)


def create_backup_archive_file(container_name, home_path, main_backup_name=None):
    location = home_path + "/"
    backup_location = f"/backups/"
    backup_name = main_backup_name
    if not main_backup_name:
        backup_name = container_name + "_" + str(datetime.date(datetime.now())) + str(
            randint(0, 999999)) + ".tar.gz"
    excludes_list = [
        "app/vendor",
        "app/node_modules",
        "app/venv",
        "app/bower_components",
        "app/.next",
        "app/.nuxt",
        "site-packages",
        "bin/",
    ]
    excludes = ""
    for exclude_item in excludes_list:
        excludes += f" --exclude='{exclude_item}'"

    os.system(
        f'cd {location} && tar --use-compress-program="pigz --best --recursive" -cf {backup_name} {excludes} $(ls -A)')
    os.system(f"cd {location} && mv {backup_name} {backup_location}")
    return [backup_name, backup_location]


def clean_out_of_space_backups(container_name):
    pass


def create_backup_task(db, container_name, platform_name, backup_name=None):
    data = {
        "name": container_name,
        "platform": {
            "name": platform_name
        },
    }
    if not os.path.exists(get_home_path(data)):
        os.mkdir(get_home_path(data))

    try:
        disk_usage = crud.get_single_service_usages_last(db, container_name).disk
    except:
        disk_usage = get_size(get_home_path(data))

    disk_usage_gb = (disk_usage / 1024) / 1024
    if disk_usage > 4 and disk_usage_gb <= 50:
        [backup_name, backup_location] = create_backup_archive_file(container_name,
                                                                    get_home_path(data),
                                                                    backup_name)
        session = boto3.session.Session()
        s3_client = session.client(
            service_name='s3',
            endpoint_url=crud.get_setting(db, "backup_server_url").value,
            aws_access_key_id=crud.get_setting(db, "backup_server_access_key").value,
            aws_secret_access_key=crud.get_setting(db, "backup_server_secret_key").value,
        )
        object_name = f"{container_name}/{backup_name}"
        backup_path = backup_location + backup_name
        bucket = crud.get_setting(db, "technical_name").value

        try:
            backup_server_bucket = crud.get_setting(db, "backup_server_bucket").value
            if len(backup_server_bucket) > 2:
                bucket = backup_server_bucket
        except:
            pass

        try:
            size = subprocess.check_output(['du', '-s', backup_path]).split()[0].decode('utf-8')
        except:
            size = 0

        if size != 0:
            try:
                s3_client.upload_file(backup_path, bucket, object_name)
            except:
                raise Exception("can't upload backup file")
            finally:
                os.remove(backup_path)

    clean_out_of_space_backups(container_name)


def normal_restore(data):
    url = data['url']
    file_name = unquote(url.split("?")[0].split("/")[-1])
    urllib.request.urlretrieve(url, file_name)
    service_root_path = get_home_path(data)
    os.system(f"rm -rf {service_root_path}")
    os.mkdir(service_root_path)

    try:
        # move backup file file to service root
        shutil.move(file_name, service_root_path)
        os.system(f"cd {service_root_path} && pigz -dc {file_name} | tar xf -")
        os.system(f"cd {service_root_path} && rm -rf {file_name}")
        if os.path.exists(service_root_path + "home"):
            os.system(f"cd {service_root_path}home/{data['name']} && mv $(ls -A) {service_root_path}")
            os.system(f"cd {service_root_path} && rm -rf home")

    finally:
        if os.path.exists(file_name):
            os.system(f"rm -rf {file_name}")

    rebuild_container(data)


def restore_mysql_backup(port, user, password, backup_path):
    ip = get_server_ip()
    sql_con = pymysql.connect(host=ip, port=int(port), user=user, password=password)
    sql_con.close()
    cmd = f"gzip -cd {backup_path} | mysql --host={ip} --port={port} --user={user} --password='{password}'"
    res_code = os.system(cmd)
    if res_code != 0:
        raise Exception("error in mysql restore")


def mysql_restore(data):
    password = "none"
    url = data['url']
    for env in data['envs']:
        if "MARIADB_ROOT_PASSWORD" in env or "MYSQL_ROOT_PASSWORD" in env:
            password = env.split("=")[1]

    file_name = unquote(url.split("?")[0].split("/")[-1])
    urllib.request.urlretrieve(url, file_name)
    service_root_path = get_home_path(data)
    os.system(f"rm -rf {service_root_path}")
    os.mkdir(service_root_path)
    rebuild_container(data)
    sleep(90)
    try:
        restore_mysql_backup(port=data['ports'][0]['outside_port'], user="root", password=password,
                             backup_path=file_name)
    except:
        raise Exception("can not restore")
    finally:
        if os.path.exists(file_name):
            os.system(f"rm -rf {file_name}")


def process_jobs(db, jobs):
    pending_jobs = []

    if jobs:
        for job in jobs:
            if job['status'] == "pending":
                pending_jobs.append(job)

        for pending_job in pending_jobs:
            if pending_job['name'] in ["restart_server", "service_create", "service_delete", 'service_action',
                                       'host_command', 'delete_core', 'debug_on', 'debug_off', 'create_backup',
                                       'restore_backup', 'limit_container', 'update_core', 'deploy_service']:

                run_at = ""
                try:
                    parsed_date = dateutil.parser.parse(pending_job['run_at'])
                    # Localize the date to UTC (if not already specified)
                    utc_date = parsed_date if parsed_date.tzinfo else pytz.utc.localize(parsed_date)

                    # Convert to Tehran timezone
                    tehran_timezone = pytz.timezone("Asia/Tehran")
                    tehran_date = utc_date.astimezone(tehran_timezone)

                    run_at = tehran_date
                except:
                    pass

                if not run_at:
                    run_at = datetime.datetime.now()
                if not crud.get_server_root_job(db, pending_job['key']):
                    crud.create_server_root_job(db, ServerRootJob(name=pending_job['name'], key=pending_job['key'],
                                                                  data=json.dumps(pending_job['data']),
                                                                  run_at=run_at))
                else:
                    server_root_job = crud.get_server_root_job(db, pending_job['key'])
                    if server_root_job.completed_at:
                        set_job_run_in_hub(db, pending_job['key'])

            elif pending_job['name'] == "normal_command":
                set_job_run_in_hub(db, pending_job['key'])
                os.system(pending_job['data']['command'])


def containers_usages(db):
    client = docker.from_env()
    containers = client.containers.list()
    container_names = [container.name for container in containers]
    all_data_usages = {}
    for container_name in container_names:
        all_container_usage = []
        usages = crud.get_single_service_usages(db, container_name)
        for usage in usages:
            all_container_usage.append({
                "ram": usage.ram,
                "cpu": usage.cpu,
                "disk": usage.disk,
                "read": usage.read,
                "write": usage.write,
                "network_rx": usage.network_rx,
                "network_tx": usage.network_tx,
            })
        all_data_usages[container_name] = all_container_usage

    return all_data_usages
