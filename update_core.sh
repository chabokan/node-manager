#!/bin/bash
set -Eeuo pipefail

# The Git pull may replace this script while it is running. Execute a stable
# snapshot so the current update completes with one version of the script.
if [[ "${UPDATE_CORE_SNAPSHOT:-0}" != '1' ]]; then
    snapshot=$(mktemp)
    trap 'rm -f "$snapshot"' EXIT
    cp "${BASH_SOURCE[0]}" "$snapshot"
    UPDATE_CORE_SNAPSHOT=1 bash "$snapshot" "$@"
    exit
fi

exec 9>"${UPDATE_CORE_LOCK_FILE:-/var/lock/chabokan-update-core.lock}"
flock -n 9 || { echo "An update is already running" >&2; exit 75; }

cd "${NODE_MANAGER_INSTALL_DIR:-/var/ch-manager}"

# Never combine a remote update with local edits to tracked files.
if ! git diff --quiet || ! git diff --cached --quiet; then
    echo "Tracked local changes prevent automatic update" >&2
    exit 1
fi

target_python=$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')
case "$target_python" in
    3.9|3.10|3.11|3.12|3.13) ;;
    *) echo "Unsupported host Python $target_python (requires 3.9–3.13)" >&2; exit 1 ;;
esac

state_file="${UPDATE_CORE_STATE_FILE:-.update-core-state}"
applied_revision=""
applied_requirements_hash=""
applied_image_id=""
if [[ -f "$state_file" ]]; then
    applied_revision=$(sed -n '1p' "$state_file")
    applied_requirements_hash=$(sed -n '2p' "$state_file")
    applied_image_id=$(sed -n '3p' "$state_file")
fi
stored_revision="$applied_revision"
old_image_name=$(docker compose config --images web)
[[ -n "$old_image_name" ]] || { echo "No web image in Compose config" >&2; exit 1; }

# Existing installations predate the state file. Trust their current checkout
# and image only when the running API is healthy, avoiding an extra restart.
if [[ -z "$applied_revision" ]]; then
    previous_container_id=$(docker compose ps -q web)
    previous_image_id=""
    if [[ -n "$previous_container_id" ]]; then
        previous_image_id=$(docker inspect "$previous_container_id" \
            --format '{{.Image}}' 2>/dev/null || true)
    fi
    if [[ -n "$previous_image_id" ]] &&
       curl -fsS --max-time 3 --connect-timeout 2 \
           "${UPDATE_HEALTH_URL:-http://127.0.0.1:8123/openapi.json}" >/dev/null 2>&1; then
        applied_revision=$(git rev-parse HEAD)
        applied_requirements_hash=$(git hash-object requirements.txt)
        applied_image_id="$previous_image_id"
    fi
fi

# Check the current image before changing the code mounted into the web container.
docker compose pull web
git pull --ff-only
new_revision=$(git rev-parse HEAD)
new_image_name=$(docker compose config --images web)
[[ -n "$new_image_name" ]] || { echo "No web image in updated Compose config" >&2; exit 1; }
if [[ "$new_image_name" != "$old_image_name" ]]; then
    docker compose pull web
fi

target_image_id=$(docker image inspect "$new_image_name" --format '{{.Id}}')
container_id=$(docker compose ps -q web)
deployed_image_id=""
if [[ -n "$container_id" ]]; then
    deployed_image_id=$(docker inspect "$container_id" --format '{{.Image}}' 2>/dev/null || true)
fi

code_changed=0
requirements_changed=0
image_changed=0
current_requirements_hash=$(git hash-object requirements.txt)
[[ "$new_revision" == "$applied_revision" ]] || code_changed=1
if [[ -n "$applied_revision" && "$current_requirements_hash" != "$applied_requirements_hash" ]]; then
    requirements_changed=1
fi
if [[ "$target_image_id" != "$deployed_image_id" ||
      "$target_image_id" != "$applied_image_id" ]]; then
    image_changed=1
fi
current_python=""
if [[ -x venv/bin/python ]]; then
    current_python=$(venv/bin/python -c 'import sys; print("%d.%d" % sys.version_info[:2])' 2>/dev/null || true)
fi

rebuild_venv=0
if [[ "$current_python" != "$target_python" ]] || (( requirements_changed )); then
    rebuild_venv=1
fi

candidate=""
backup=""
restore_previous_venv() {
    if [[ -n "$candidate" && -d "$candidate" ]]; then
        rm -rf "$candidate"
    fi
    if [[ -n "$backup" && -d "$backup" ]]; then
        rm -rf venv
        mv "$backup" venv
    fi
}
trap restore_previous_venv ERR

if (( rebuild_venv )); then
    echo "Building host virtual environment for Python $target_python" >&2
    candidate="venv.candidate.$(date +%s).$$"
    python3 -m venv "$candidate"
    "$candidate/bin/python" -m pip install -r requirements.txt
    if [[ -d venv ]]; then
        backup="venv.backup.$(date +%s).$$"
        mv venv "$backup"
    fi
    mv "$candidate" venv
    candidate=""
fi

if (( code_changed || rebuild_venv )); then
    venv/bin/python -m alembic upgrade head
fi

wait_for_web() {
    local attempt
    for (( attempt=1; attempt<=30; attempt++ )); do
        if curl -fsS --max-time 3 --connect-timeout 2 \
            "${UPDATE_HEALTH_URL:-http://127.0.0.1:8123/openapi.json}" >/dev/null 2>&1; then
            return 0
        fi
        sleep 1
    done
    return 1
}

if (( code_changed || image_changed )); then
    echo "Applying node-manager update (code=$code_changed, image=$image_changed)" >&2
    docker compose up -d --force-recreate web
    if ! wait_for_web; then
        echo "Updated web service did not become healthy within 30 seconds" >&2
        # If only the image changed, restore the last known healthy image.
        if (( ! code_changed )) && [[ -n "$applied_image_id" &&
              "$applied_image_id" != "$target_image_id" ]] &&
           docker image inspect "$applied_image_id" >/dev/null 2>&1; then
            echo "Attempting rollback to image $applied_image_id" >&2
            if docker image tag "$applied_image_id" "$new_image_name" &&
               docker compose up -d --force-recreate --pull never web &&
               wait_for_web; then
                echo "Previous web image restored; the new image will be retried later" >&2
            else
                echo "WARN: could not restore the previous web image" >&2
            fi
        fi
        # Keep the host venv compatible with the current checkout.
        if [[ -n "$backup" ]]; then
            rm -rf "$backup"
            backup=""
        fi
        trap - ERR
        exit 1
    fi
else
    echo "Node-manager code and web image are already current" >&2
fi

if [[ -n "$backup" ]]; then
    rm -rf "$backup"
    backup=""
fi
trap - ERR

# The state is written only after dependencies, migrations and the web image
# have been applied. A failed run remains eligible for the next nightly retry.
if [[ ! -f "$state_file" || -z "$stored_revision" ||
      "$new_revision" != "$applied_revision" ||
      "$current_requirements_hash" != "$applied_requirements_hash" ||
      "$target_image_id" != "$applied_image_id" ]]; then
    state_tmp="${state_file}.tmp.$$"
    printf '%s\n%s\n%s\n' "$new_revision" "$current_requirements_hash" \
        "$target_image_id" > "$state_tmp"
    mv "$state_tmp" "$state_file"
fi

# Keep the connector-managed firewall allowlist refreshed independently of
# node-manager code/image changes, as the previous nightly update did.
connector_dir="${SERVER_CONNECTOR_INSTALL_DIR:-/var/server-connector}"
if [[ -f "$connector_dir/utilities/firewall.sh" ]]; then
    if ! bash "$connector_dir/utilities/firewall.sh"; then
        echo "WARN: firewall allowlist refresh failed; node-manager update remains applied" >&2
    fi
fi
