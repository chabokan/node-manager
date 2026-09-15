#!/bin/bash
set -euo pipefail

PY39_BIN="${1:?Pass a Python 3.9 executable as argument 1}"
PY313_BIN="${2:?Pass a Python 3.13 executable as argument 2}"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TEST_DIR=$(mktemp -d)
trap 'rm -rf "$TEST_DIR"' EXIT
mkdir -p "$TEST_DIR/bin" "$TEST_DIR/seed"
git init --bare --initial-branch=main "$TEST_DIR/remote.git" >/dev/null
git init -b main "$TEST_DIR/seed" >/dev/null
git -C "$TEST_DIR/seed" config user.name 'Update Test'
git -C "$TEST_DIR/seed" config user.email 'update-test@example.invalid'
: > "$TEST_DIR/seed/requirements.txt"
cat > "$TEST_DIR/seed/docker-compose.yml" <<'EOF'
services:
  web:
    image: docker.chabokan.net/chabokan/node-manager
EOF
git -C "$TEST_DIR/seed" add requirements.txt docker-compose.yml
git -C "$TEST_DIR/seed" commit -m 'Initial manager' >/dev/null
git -C "$TEST_DIR/seed" remote add origin "$TEST_DIR/remote.git"
git -C "$TEST_DIR/seed" push -u origin main >/dev/null
git clone "$TEST_DIR/remote.git" "$TEST_DIR/manager" >/dev/null
printf 'sha256:image-a\n' > "$TEST_DIR/remote-image-id"

cat > "$TEST_DIR/bin/python3" <<'EOF'
#!/bin/bash
set -euo pipefail
"$TEST_PYTHON_BIN" "$@"
if [[ "${1:-}" == "-m" && "${2:-}" == "venv" ]]; then
    env_dir="${@: -1}"
    "$TEST_PYTHON_BIN" - "$env_dir" <<'PY'
from pathlib import Path
import sys

site = Path(sys.argv[1]) / 'lib' / ('python%d.%d' % sys.version_info[:2]) / 'site-packages'
package = site / 'alembic'
package.mkdir(parents=True, exist_ok=True)
(package / '__init__.py').write_text('')
(package / '__main__.py').write_text('import os\nwith open(os.environ["TEST_ALEMBIC_LOG"], "a") as log: log.write("migration\\n")\n')
PY
fi
EOF
cat > "$TEST_DIR/bin/docker" <<'EOF'
#!/bin/bash
set -euo pipefail
printf '%s\n' "$*" >> "$TEST_DOCKER_CALLS"
case "${1:-} ${2:-} ${3:-}" in
    'compose config --images')
        sed -n 's/^[[:space:]]*image: //p' "$TEST_MANAGER_DIR/docker-compose.yml"
        ;;
    'compose pull web')
        cp "$TEST_REMOTE_IMAGE_ID_FILE" "$TEST_LOCAL_IMAGE_ID_FILE"
        ;;
    'image inspect '*)
        if [[ "$3" == sha256:* ]]; then
            printf '%s\n' "$3"
        else
            cat "$TEST_LOCAL_IMAGE_ID_FILE"
        fi
        ;;
    'image tag '*)
        printf '%s\n' "$3" > "$TEST_LOCAL_IMAGE_ID_FILE"
        ;;
    'compose ps -q')
        [[ ! -f "$TEST_DEPLOYED_IMAGE_ID_FILE" ]] || printf 'test-web\n'
        ;;
    'inspect test-web --format')
        cat "$TEST_DEPLOYED_IMAGE_ID_FILE"
        ;;
    'compose up -d')
        cp "$TEST_LOCAL_IMAGE_ID_FILE" "$TEST_DEPLOYED_IMAGE_ID_FILE"
        ;;
    *)
        echo "Unexpected Docker command: $*" >&2
        exit 1
        ;;
esac
EOF
cat > "$TEST_DIR/bin/flock" <<'EOF'
#!/bin/bash
[[ "${TEST_FLOCK_BUSY:-0}" != '1' ]]
EOF
cat > "$TEST_DIR/bin/curl" <<'EOF'
#!/bin/bash
if [[ "${TEST_HEALTH_FAIL:-0}" == '1' ]] &&
   [[ "$(cat "$TEST_DEPLOYED_IMAGE_ID_FILE")" == "$(cat "$TEST_REMOTE_IMAGE_ID_FILE")" ]]; then
    exit 1
fi
exit 0
EOF
cat > "$TEST_DIR/bin/sleep" <<'EOF'
#!/bin/bash
exit 0
EOF
chmod +x "$TEST_DIR/bin/"*

run_update() {
    TEST_PYTHON_BIN="$1" PATH="$TEST_DIR/bin:$PATH" \
    TEST_MANAGER_DIR="$TEST_DIR/manager" \
    TEST_REMOTE_IMAGE_ID_FILE="$TEST_DIR/remote-image-id" \
    TEST_LOCAL_IMAGE_ID_FILE="$TEST_DIR/local-image-id" \
    TEST_DEPLOYED_IMAGE_ID_FILE="$TEST_DIR/deployed-image-id" \
    TEST_DOCKER_CALLS="$TEST_DIR/docker-calls" \
    TEST_ALEMBIC_LOG="$TEST_DIR/alembic-calls" \
    TEST_HEALTH_FAIL="${TEST_HEALTH_FAIL:-0}" \
    NODE_MANAGER_INSTALL_DIR="$TEST_DIR/manager" \
    UPDATE_CORE_LOCK_FILE="$TEST_DIR/update.lock" \
    SERVER_CONNECTOR_INSTALL_DIR="${TEST_CONNECTOR_DIR:-$TEST_DIR/missing-connector}" \
    bash "$REPO_DIR/update_core.sh" > "$TEST_DIR/update.log" 2>&1
}

count_recreates() { grep -c '^compose up -d' "$TEST_DIR/docker-calls" || true; }
count_migrations() { grep -c '^migration$' "$TEST_DIR/alembic-calls" || true; }

run_update "$PY39_BIN"
[[ -x "$TEST_DIR/manager/venv/bin/python" ]]
[[ "$("$TEST_DIR/manager/venv/bin/python" -c 'import sys; print("%d.%d" % sys.version_info[:2])')" == '3.9' ]]
[[ $(count_recreates) == 1 ]]
[[ $(count_migrations) == 1 ]]
run_update "$PY313_BIN"
[[ "$("$TEST_DIR/manager/venv/bin/python" -c 'import sys; print("%d.%d" % sys.version_info[:2])')" == '3.13' ]]
[[ -z "$(find "$TEST_DIR/manager" -maxdepth 1 -name 'venv.backup.*' -print)" ]]
[[ $(count_recreates) == 1 ]]
[[ $(count_migrations) == 2 ]]

touch "$TEST_DIR/manager/venv/marker"
run_update "$PY313_BIN"
[[ $(count_recreates) == 1 ]]
[[ $(count_migrations) == 2 ]]
[[ -f "$TEST_DIR/manager/venv/marker" ]]
grep -F 'already current' "$TEST_DIR/update.log" >/dev/null

rm "$TEST_DIR/manager/.update-core-state"
run_update "$PY313_BIN"
[[ -f "$TEST_DIR/manager/.update-core-state" ]]
[[ $(count_recreates) == 1 ]]
[[ $(count_migrations) == 2 ]]
[[ $(sed -n '3p' "$TEST_DIR/manager/.update-core-state") == 'sha256:image-a' ]]

printf 'sha256:image-b\n' > "$TEST_DIR/remote-image-id"
run_update "$PY313_BIN"
[[ $(count_recreates) == 2 ]]
[[ $(count_migrations) == 2 ]]
[[ -f "$TEST_DIR/manager/venv/marker" ]]
[[ $(cat "$TEST_DIR/deployed-image-id") == 'sha256:image-b' ]]

printf 'changed code\n' > "$TEST_DIR/seed/app.py"
git -C "$TEST_DIR/seed" add app.py
git -C "$TEST_DIR/seed" commit -m 'Update code only' >/dev/null
git -C "$TEST_DIR/seed" push origin main >/dev/null
run_update "$PY313_BIN"
[[ $(git -C "$TEST_DIR/manager" rev-parse HEAD) == $(git -C "$TEST_DIR/seed" rev-parse HEAD) ]]
[[ $(count_recreates) == 3 ]]
[[ $(count_migrations) == 3 ]]
[[ -f "$TEST_DIR/manager/venv/marker" ]]

healthy_state_before=$(cat "$TEST_DIR/manager/.update-core-state")
printf 'sha256:image-c\n' > "$TEST_DIR/remote-image-id"
if TEST_HEALTH_FAIL=1 run_update "$PY313_BIN"; then
    echo 'Expected an unhealthy web update to fail' >&2
    exit 1
fi
[[ $(count_recreates) == 5 ]]
[[ $(cat "$TEST_DIR/manager/.update-core-state") == "$healthy_state_before" ]]
grep -F 'did not become healthy' "$TEST_DIR/update.log" >/dev/null
grep -F 'Previous web image restored' "$TEST_DIR/update.log" >/dev/null
[[ $(cat "$TEST_DIR/deployed-image-id") == 'sha256:image-b' ]]

run_update "$PY313_BIN"
[[ $(count_recreates) == 6 ]]
[[ $(count_migrations) == 3 ]]
[[ $(sed -n '3p' "$TEST_DIR/manager/.update-core-state") == 'sha256:image-c' ]]

mkdir -p "$TEST_DIR/failing-connector/utilities"
cat > "$TEST_DIR/failing-connector/utilities/firewall.sh" <<'EOF'
#!/bin/bash
exit 1
EOF
if ! TEST_CONNECTOR_DIR="$TEST_DIR/failing-connector" run_update "$PY313_BIN"; then
    echo 'A connector firewall failure should not fail a current core update' >&2
    exit 1
fi
[[ $(count_recreates) == 6 ]]
grep -F 'firewall allowlist refresh failed' "$TEST_DIR/update.log" >/dev/null

applied_state_before=$(cat "$TEST_DIR/manager/.update-core-state")
printf '??invalid requirement\n' > "$TEST_DIR/seed/requirements.txt"
git -C "$TEST_DIR/seed" add requirements.txt
git -C "$TEST_DIR/seed" commit -m 'Bad requirements' >/dev/null
git -C "$TEST_DIR/seed" push origin main >/dev/null
if run_update "$PY313_BIN"; then
    printf 'expected invalid requirements to fail the update\n' >&2
    exit 1
fi
[[ $(cat "$TEST_DIR/manager/.update-core-state") == "$applied_state_before" ]]
[[ -f "$TEST_DIR/manager/venv/marker" ]]
[[ $(count_recreates) == 6 ]]

if run_update "$PY313_BIN"; then
    echo 'Expected a failed dependency update to be retried' >&2
    exit 1
fi
grep -F 'Building host virtual environment' "$TEST_DIR/update.log" >/dev/null
[[ $(cat "$TEST_DIR/manager/.update-core-state") == "$applied_state_before" ]]

if run_update "$PY39_BIN"; then
    printf 'expected Python-version switch with invalid requirements to fail\n' >&2
    exit 1
fi
[[ "$("$TEST_DIR/manager/venv/bin/python" -c 'import sys; print("%d.%d" % sys.version_info[:2])')" == '3.13' ]]
[[ -f "$TEST_DIR/manager/venv/marker" ]]
[[ $(count_recreates) == 6 ]]
[[ -z "$(find "$TEST_DIR/manager" -maxdepth 1 -name 'venv.candidate.*' -print)" ]]

if TEST_FLOCK_BUSY=1 run_update "$PY313_BIN"; then
    echo 'Expected a busy update lock to fail' >&2
    exit 1
else
    [[ $? == 75 ]]
fi

printf 'local change\n' >> "$TEST_DIR/manager/docker-compose.yml"
if run_update "$PY313_BIN"; then
    echo 'Expected tracked local changes to block the update' >&2
    exit 1
fi
grep -F 'Tracked local changes' "$TEST_DIR/update.log" >/dev/null

printf 'Automatic code/image checks, virtualenv rebuild and rollback: ok\n'
