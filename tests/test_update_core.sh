#!/bin/bash
set -euo pipefail

PY39_BIN="${1:?Pass a Python 3.9 executable as argument 1}"
PY313_BIN="${2:?Pass a Python 3.13 executable as argument 2}"
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TEST_DIR=$(mktemp -d)
trap 'rm -rf "$TEST_DIR"' EXIT
mkdir -p "$TEST_DIR/bin" "$TEST_DIR/manager"
: > "$TEST_DIR/manager/requirements.txt"

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
(package / '__main__.py').write_text('raise SystemExit(0)\n')
PY
fi
EOF
cat > "$TEST_DIR/bin/git" <<'EOF'
#!/bin/bash
exit 0
EOF
cat > "$TEST_DIR/bin/docker" <<'EOF'
#!/bin/bash
exit 0
EOF
cat > "$TEST_DIR/bin/flock" <<'EOF'
#!/bin/bash
exit 0
EOF
chmod +x "$TEST_DIR/bin/"*

run_update() {
    TEST_PYTHON_BIN="$1" PATH="$TEST_DIR/bin:$PATH" \
    NODE_MANAGER_INSTALL_DIR="$TEST_DIR/manager" \
    UPDATE_CORE_LOCK_FILE="$TEST_DIR/update.lock" \
    SERVER_CONNECTOR_INSTALL_DIR="$TEST_DIR/missing-connector" \
    bash "$REPO_DIR/update_core.sh" > "$TEST_DIR/update.log" 2>&1
}

run_update "$PY39_BIN"
[[ -x "$TEST_DIR/manager/venv/bin/python" ]]
[[ "$("$TEST_DIR/manager/venv/bin/python" -c 'import sys; print("%d.%d" % sys.version_info[:2])')" == '3.9' ]]
run_update "$PY313_BIN"
[[ "$("$TEST_DIR/manager/venv/bin/python" -c 'import sys; print("%d.%d" % sys.version_info[:2])')" == '3.13' ]]
[[ -z "$(find "$TEST_DIR/manager" -maxdepth 1 -name 'venv.backup.*' -print)" ]]

printf '??invalid requirement\n' > "$TEST_DIR/manager/requirements.txt"
if run_update "$PY39_BIN"; then
    printf 'expected invalid requirements to fail the update\n' >&2
    exit 1
fi
[[ "$("$TEST_DIR/manager/venv/bin/python" -c 'import sys; print("%d.%d" % sys.version_info[:2])')" == '3.13' ]]
printf 'Python-version switch and failed-update rollback: ok\n'
