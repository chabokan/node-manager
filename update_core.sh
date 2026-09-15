#!/bin/bash
set -Eeuo pipefail

exec 9>"${UPDATE_CORE_LOCK_FILE:-/var/lock/chabokan-update-core.lock}"
flock -n 9 || exit 0

cd "${NODE_MANAGER_INSTALL_DIR:-/var/ch-manager}"

# Check the image before changing the code mounted into the running container.
docker compose pull web

# A local change must be reviewed instead of silently discarded by an update.
git pull --ff-only

# Use one virtual environment on Ubuntu and Debian, including Python 3.13 hosts.
venv_backup=""
restore_previous_venv() {
    if [[ -n "$venv_backup" && -d "$venv_backup" ]]; then
        rm -rf venv
        mv "$venv_backup" venv
    fi
}
trap restore_previous_venv ERR

target_python=$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')
current_python=""
if [[ -x venv/bin/python ]]; then
    current_python=$(venv/bin/python -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null || true)
fi
if [[ -d venv && "$current_python" != "$target_python" ]]; then
    venv_backup="venv.backup.$(date +%s).$$"
    mv venv "$venv_backup"
fi
if [[ ! -d venv ]]; then
    python3 -m venv venv
fi
venv/bin/python -m pip install -r requirements.txt
venv/bin/python -m alembic upgrade head

docker compose up -d --force-recreate web

if [[ -n "$venv_backup" ]]; then
    rm -rf "$venv_backup"
    venv_backup=""
fi
trap - ERR

connector_dir="${SERVER_CONNECTOR_INSTALL_DIR:-/var/server-connector}"
if [[ -f "$connector_dir/utilities/firewall.sh" ]]; then
    bash "$connector_dir/utilities/firewall.sh"
fi
