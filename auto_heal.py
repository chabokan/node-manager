"""Hourly self-healing for a Chabokan node.

The web container runs its own cron, so it cannot recover itself when the API
process hangs. This module therefore runs from the host worker
(``server-queue.py``), which the connector schedules every minute and which has
``docker compose`` available, and gates itself to at most one run per hour.

Every action is bounded and idempotent. Recoverable problems are repaired, and
everything else is logged for the operator instead of being retried forever.
"""

import json
import logging
import os
import subprocess
import tempfile
import time
from datetime import timedelta

import requests

import crud
from api.helper import set_job_run_in_hub
from core.clock import tehran_now
from core.logging_setup import log_event
from host_inventory import read_host_inventory, write_host_inventory
from server_queue import failure_reason_for


logger = logging.getLogger("auto_heal")

INTERVAL_SECONDS = 3600
LOCK_STALE_SECONDS = 1800
MAX_FUTURE_DAYS = 7
REPORT_GRACE_SECONDS = 300
HUB_STALE_SECONDS = 1800
HEALTH_FAILURES_BEFORE_RESTART = 2
RESTART_COOLDOWN_SECONDS = 600
MAX_RESTARTS_PER_HOUR = 2

DEFAULT_STATE_FILE = "/var/lib/chabokan-manager/auto-heal.json"
DEFAULT_HEALTH_URL = "http://127.0.0.1:8123/openapi.json"
DEFAULT_COMPOSE_DIR = "/var/ch-manager"


def _env(name, default):
    value = os.environ.get(name)
    return value if value else default


def _as_float(value):
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


def _state_path(override=None):
    return override or _env("AUTO_HEAL_STATE_FILE", DEFAULT_STATE_FILE)


def _load_state(path):
    try:
        with open(path) as stream:
            state = json.load(stream)
        return state if isinstance(state, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_state(path, state):
    directory = os.path.dirname(path) or "."
    try:
        os.makedirs(directory, mode=0o700, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=".auto-heal-", dir=directory)
        try:
            with os.fdopen(descriptor, "w") as stream:
                json.dump(state, stream)
            os.chmod(temporary, 0o600)
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
    except OSError as exc:
        log_event(logger, "auto_heal_state_write_failed", level=logging.ERROR,
                  path=path, error=type(exc).__name__)


def _record(report, action, **details):
    entry = {"action": action}
    entry.update(details)
    report["actions"].append(entry)
    log_event(logger, "auto_heal_action", action=action, **details)


def _check_web_health(url=None):
    url = url or _env("AUTO_HEAL_HEALTH_URL", DEFAULT_HEALTH_URL)
    try:
        response = requests.get(url, timeout=5)
        return response.status_code == 200
    except requests.RequestException:
        return False


def _probe_hub(base_hub_url):
    """Reachability from the host: any HTTP response means the hub is reachable."""
    try:
        requests.get(f"https://{base_hub_url}/", timeout=10, allow_redirects=False)
        return True
    except requests.RequestException:
        return False


def _probe_hub_from_web(base_hub_url):
    """Ask the API container whether it can reach the hub.

    The host may reach the hub while the container's own network namespace
    cannot; only that case benefits from recreating the container.
    """
    directory = _env("AUTO_HEAL_COMPOSE_DIR", DEFAULT_COMPOSE_DIR)
    probe = ("import sys, requests; requests.get('https://' + sys.argv[1] + '/', "
             "timeout=5)")
    try:
        result = subprocess.run(
            ["docker", "compose", "exec", "-T", "web", "python", "-c", probe, base_hub_url],
            cwd=directory, capture_output=True, text=True, timeout=60, check=False)
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def _restart_web():
    directory = _env("AUTO_HEAL_COMPOSE_DIR", DEFAULT_COMPOSE_DIR)
    try:
        result = subprocess.run(["docker", "compose", "restart", "web"], cwd=directory,
                                capture_output=True, text=True, timeout=180, check=False)
    except (OSError, subprocess.SubprocessError) as exc:
        log_event(logger, "web_restart_failed", level=logging.ERROR,
                  error=type(exc).__name__)
        return False
    if result.returncode:
        log_event(logger, "web_restart_failed", level=logging.ERROR,
                  returncode=result.returncode,
                  error=(result.stderr or "").strip()[:200])
        return False
    log_event(logger, "web_restarted", level=logging.WARNING)
    return True


def _report_finished_jobs(db):
    jobs = crud.get_unreported_finished_root_jobs(
        db, tehran_now() - timedelta(seconds=REPORT_GRACE_SECONDS))
    reported = 0
    for job in jobs:
        reason = failure_reason_for(job.name) if job.status == "failed" else None
        try:
            set_job_run_in_hub(db, job.key, job.status, failure_reason=reason)
        except Exception as exc:
            log_event(logger, "job_report_failed", key=job.key, status=job.status,
                      error=type(exc).__name__, retry="next_hour")
            break
        reported += 1
    return reported, len(jobs) - reported


def _handle_web_health(state, report, healthy, now):
    if healthy:
        state["web_failures"] = 0
        return
    failures = int(state.get("web_failures") or 0) + 1
    state["web_failures"] = failures
    if failures < HEALTH_FAILURES_BEFORE_RESTART:
        _record(report, "web_health_failure", count=failures)
        return
    restarts = [value for value in state.get("web_restarts", [])
                if now - _as_float(value) < INTERVAL_SECONDS]
    state["web_restarts"] = restarts
    if now - _as_float(state.get("last_web_restart")) < RESTART_COOLDOWN_SECONDS:
        _record(report, "web_restart_cooldown")
        return
    if len(restarts) >= MAX_RESTARTS_PER_HOUR:
        _record(report, "web_restart_limit_reached")
        return
    if _restart_web():
        state["last_web_restart"] = now
        state["web_restarts"] = restarts + [now]
        state["web_failures"] = 0
        _record(report, "restarted_web")


def _hub_state(db, report, now):
    """Detect a hub connection that the API stopped refreshing.

    A stale ``hub_last_seen`` is only meaningful once it has been recorded.
    A hub that is unreachable from the host is a network or hub outage and is
    only reported. When the host can reach the hub but the API container cannot,
    the container's network is the problem, so it escalates to the health path.
    When neither side is blocked, the API is simply not syncing (for example a
    rejected token) and a restart would not help.
    """
    token = crud.get_setting(db, "token")
    hub = crud.get_setting(db, "base_hub_url")
    if not token or not hub or not hub.value:
        return None
    last_seen = crud.get_setting(db, "hub_last_seen")
    if last_seen is None or now - _as_float(last_seen.value) <= HUB_STALE_SECONDS:
        return None
    if not _probe_hub(hub.value):
        _record(report, "hub_unreachable")
        return True
    if _probe_hub_from_web(hub.value):
        _record(report, "hub_stale_app_side")
        return True
    _record(report, "hub_unreachable_from_web")
    return False


def run_auto_heal(db, force=False, now=None, state_path=None, host_mode=True):
    now = time.time() if now is None else now
    path = _state_path(state_path)
    state = _load_state(path)
    if not force and now - _as_float(state.get("last_run")) < INTERVAL_SECONDS:
        log_event(logger, "auto_heal_skipped", reason="interval", host_mode=host_mode)
        return {"skipped": True, "reason": "interval", "actions": []}

    log_event(logger, "auto_heal_started", host_mode=host_mode)
    report = {"skipped": False, "actions": []}

    unlocked = crud.unlock_stale_server_root_jobs(
        db, tehran_now() - timedelta(seconds=LOCK_STALE_SECONDS))
    if unlocked:
        _record(report, "unlocked_stale_jobs", count=unlocked)

    clamped = crud.clamp_future_server_root_jobs(
        db, tehran_now() + timedelta(days=MAX_FUTURE_DAYS))
    if clamped:
        _record(report, "clamped_future_jobs", count=clamped)

    reported, pending = _report_finished_jobs(db)
    if reported:
        _record(report, "reported_finished_jobs", count=reported)
    if pending:
        _record(report, "pending_hub_reports", count=pending)

    if host_mode:
        try:
            if read_host_inventory() is None:
                write_host_inventory()
                _record(report, "refreshed_host_inventory")
        except OSError as exc:
            log_event(logger, "host_inventory_refresh_failed", level=logging.ERROR,
                      error=type(exc).__name__)

    healthy = _check_web_health() if host_mode else True
    if not healthy:
        _record(report, "web_health_unhealthy")

    hub_result = _hub_state(db, report, now)
    if hub_result is False:
        healthy = False

    if host_mode:
        _handle_web_health(state, report, healthy, now)

    state["last_run"] = now
    _save_state(path, state)
    log_event(logger, "auto_heal_finished", actions=len(report["actions"]),
              repairable=sum(1 for entry in report["actions"]
                             if entry["action"] not in ("web_health_failure",
                                                        "web_restart_cooldown",
                                                        "web_restart_limit_reached")))
    return report


def main(argv=None):
    import argparse

    parser = argparse.ArgumentParser(description="Run node self-healing once.")
    parser.add_argument("--force", action="store_true",
                        help="Ignore the hourly interval and heal now.")
    arguments = parser.parse_args(argv)
    from core.logging_setup import configure_logging
    from core.db import SessionLocal
    configure_logging(service="host")
    with SessionLocal() as db:
        report = run_auto_heal(db, force=arguments.force)
    return 0 if not report.get("skipped") else 0


if __name__ == "__main__":
    raise SystemExit(main())
