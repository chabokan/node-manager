import datetime
import json
import os
import tempfile
import unittest
from types import SimpleNamespace
from unittest import mock

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import auto_heal
import crud
from core.clock import tehran_now
from models import Base, ServerRootJob, Setting


_FRESH = object()


class AutoHealTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine)()
        handle, self.state_path = tempfile.mkstemp(prefix="auto-heal-")
        os.close(handle)
        self.now = 1_700_000_000.0

    def tearDown(self):
        self.db.close()
        self.engine.dispose()
        if os.path.exists(self.state_path):
            os.unlink(self.state_path)

    def run_heal(self, healthy=True, inventory=_FRESH, hub_reachable=True, hub_from_web=False,
                 force=True, now=None, report_side_effect=None):
        inventory = {"disks": {}} if inventory is _FRESH else inventory
        with mock.patch.object(auto_heal, "write_host_inventory") as write_inventory, \
             mock.patch.object(auto_heal, "read_host_inventory", return_value=inventory), \
             mock.patch.object(auto_heal, "_check_web_health", return_value=healthy) as health, \
             mock.patch.object(auto_heal, "_probe_hub", return_value=hub_reachable) as probe, \
             mock.patch.object(auto_heal, "_probe_hub_from_web",
                               return_value=hub_from_web) as web_probe, \
             mock.patch.object(auto_heal, "_restart_web", return_value=True) as restart, \
             mock.patch.object(auto_heal, "set_job_run_in_hub",
                               side_effect=report_side_effect) as report_hub:
            report = auto_heal.run_auto_heal(
                self.db, force=force, now=self.now if now is None else now,
                state_path=self.state_path)
        return report, SimpleNamespace(write_inventory=write_inventory, health=health,
                                       probe=probe, web_probe=web_probe, restart=restart,
                                       report_hub=report_hub)

    def actions(self, report):
        return [entry["action"] for entry in report["actions"]]

    def add_job(self, **values):
        values.setdefault("data", "{}")
        values.setdefault("status", "pending")
        values.setdefault("locked", False)
        values.setdefault("run_count", 0)
        job = ServerRootJob(**values)
        self.db.add(job)
        self.db.commit()
        return job

    def test_interval_gate_skips_a_recent_run(self):
        auto_heal._save_state(self.state_path, {"last_run": self.now - 10})
        report = auto_heal.run_auto_heal(self.db, force=False, now=self.now,
                                         state_path=self.state_path)
        self.assertTrue(report["skipped"])

    def test_force_ignores_the_interval_gate(self):
        auto_heal._save_state(self.state_path, {"last_run": self.now - 10})
        report, _ = self.run_heal(force=True)
        self.assertFalse(report["skipped"])

    def test_stale_lock_is_released_and_future_run_at_is_clamped(self):
        stale = self.add_job(name="host_command", key="stale", locked=True,
                             locked_at=tehran_now() - datetime.timedelta(minutes=31),
                             run_at=tehran_now() - datetime.timedelta(minutes=1))
        future = self.add_job(name="host_command", key="future",
                              run_at=tehran_now() + datetime.timedelta(days=30))
        report, _ = self.run_heal()
        self.db.refresh(stale)
        self.db.refresh(future)
        self.assertFalse(stale.locked)
        self.assertIsNone(stale.locked_at)
        self.assertLessEqual(future.run_at, tehran_now() + datetime.timedelta(seconds=1))
        self.assertIn("unlocked_stale_jobs", self.actions(report))
        self.assertIn("clamped_future_jobs", self.actions(report))

    def test_recent_lock_is_left_alone(self):
        fresh = self.add_job(name="host_command", key="fresh", locked=True,
                             locked_at=tehran_now() - datetime.timedelta(minutes=5))
        self.run_heal()
        self.db.refresh(fresh)
        self.assertTrue(fresh.locked)

    def test_unreported_finished_job_is_reported_and_marked(self):
        job = self.add_job(name="host_command", key="done", status="success", reported=False,
                           completed_at=tehran_now() - datetime.timedelta(minutes=10))

        def report(db, key, status="success", failure_reason=None):
            crud.mark_server_root_job_reported(db, key)

        report_result, mocks = self.run_heal(report_side_effect=report)
        mocks.report_hub.assert_called_once_with(self.db, "done", "success", failure_reason=None)
        self.db.refresh(job)
        self.assertTrue(job.reported)
        self.assertIn("reported_finished_jobs", self.actions(report_result))

    def test_failed_job_is_reported_with_a_safe_reason(self):
        self.add_job(name="create_backup", key="backup", status="failed", reported=False,
                     completed_at=tehran_now() - datetime.timedelta(minutes=10))
        _, mocks = self.run_heal()
        mocks.report_hub.assert_called_once_with(self.db, "backup", "failed",
                                                 failure_reason="backup_failed")

    def test_unreachable_hub_keeps_the_job_unreported(self):
        job = self.add_job(name="host_command", key="offline", status="success", reported=False,
                           completed_at=tehran_now() - datetime.timedelta(minutes=10))
        with mock.patch.object(auto_heal, "write_host_inventory"), \
             mock.patch.object(auto_heal, "read_host_inventory", return_value={"disks": {}}), \
             mock.patch.object(auto_heal, "_check_web_health", return_value=True), \
             mock.patch.object(auto_heal, "_probe_hub", return_value=True), \
             mock.patch.object(auto_heal, "_restart_web", return_value=True), \
             mock.patch.object(auto_heal, "set_job_run_in_hub", side_effect=OSError("offline")):
            report = auto_heal.run_auto_heal(self.db, force=True, now=self.now,
                                             state_path=self.state_path)
        self.db.refresh(job)
        self.assertFalse(job.reported)
        self.assertIn("pending_hub_reports", self.actions(report))

    def test_two_consecutive_health_failures_restart_the_web_service(self):
        first, mocks = self.run_heal(healthy=False)
        mocks.restart.assert_not_called()
        self.assertIn("web_health_failure", self.actions(first))
        second, mocks = self.run_heal(healthy=False, now=self.now + 60)
        mocks.restart.assert_called_once()
        self.assertIn("restarted_web", self.actions(second))

    def test_restart_respects_the_cooldown(self):
        auto_heal._save_state(self.state_path, {
            "last_run": self.now - 4000, "web_failures": 1,
            "last_web_restart": self.now - 100, "web_restarts": [self.now - 100]})
        report, mocks = self.run_heal(healthy=False)
        mocks.restart.assert_not_called()
        self.assertIn("web_restart_cooldown", self.actions(report))

    def test_restart_is_capped_per_hour(self):
        auto_heal._save_state(self.state_path, {
            "last_run": self.now - 4000, "web_failures": 1,
            "last_web_restart": self.now - 1000,
            "web_restarts": [self.now - 100, self.now - 200]})
        report, mocks = self.run_heal(healthy=False)
        mocks.restart.assert_not_called()
        self.assertIn("web_restart_limit_reached", self.actions(report))

    def test_healthy_web_resets_failure_count(self):
        auto_heal._save_state(self.state_path, {"last_run": self.now - 4000, "web_failures": 3})
        self.run_heal(healthy=True)
        self.assertEqual(auto_heal._load_state(self.state_path)["web_failures"], 0)

    def test_missing_host_inventory_is_refreshed(self):
        report, mocks = self.run_heal(inventory=None)
        mocks.write_inventory.assert_called_once()
        self.assertIn("refreshed_host_inventory", self.actions(report))

    def test_fresh_host_inventory_is_not_rewritten(self):
        _, mocks = self.run_heal(inventory={"disks": {"/dev/vda": {"usage_available": True}}})
        mocks.write_inventory.assert_not_called()

    def test_hub_never_seen_is_not_treated_as_stale(self):
        self.db.add_all([Setting(key="token", value="token"),
                         Setting(key="base_hub_url", value="hub.example")])
        self.db.commit()
        report, mocks = self.run_heal()
        mocks.probe.assert_not_called()
        self.assertNotIn("hub_stale_app_side", self.actions(report))

    def test_stale_hub_with_reachable_host_but_blocked_container_is_a_health_failure(self):
        self.db.add_all([Setting(key="token", value="token"),
                         Setting(key="base_hub_url", value="hub.example"),
                         Setting(key="hub_last_seen", value=str(self.now - 3600))])
        self.db.commit()
        report, mocks = self.run_heal(healthy=True, hub_reachable=True, hub_from_web=False)
        self.assertIn("hub_unreachable_from_web", self.actions(report))
        self.assertEqual(auto_heal._load_state(self.state_path)["web_failures"], 1)

    def test_stale_hub_reachable_from_both_sides_is_only_reported(self):
        self.db.add_all([Setting(key="token", value="token"),
                         Setting(key="base_hub_url", value="hub.example"),
                         Setting(key="hub_last_seen", value=str(self.now - 3600))])
        self.db.commit()
        report, mocks = self.run_heal(healthy=True, hub_reachable=True, hub_from_web=True)
        self.assertIn("hub_stale_app_side", self.actions(report))
        self.assertNotIn("web_health_failure", self.actions(report))
        self.assertEqual(auto_heal._load_state(self.state_path).get("web_failures", 0), 0)

    def test_unreachable_hub_is_reported_without_a_restart(self):
        self.db.add_all([Setting(key="token", value="token"),
                         Setting(key="base_hub_url", value="hub.example"),
                         Setting(key="hub_last_seen", value=str(self.now - 3600))])
        self.db.commit()
        report, mocks = self.run_heal(healthy=True, hub_reachable=False)
        mocks.restart.assert_not_called()
        self.assertIn("hub_unreachable", self.actions(report))
        self.assertNotIn("web_health_failure", self.actions(report))

    def test_health_url_failure_is_an_unhealthy_web(self):
        with mock.patch.object(auto_heal.requests, "get",
                               side_effect=auto_heal.requests.ConnectionError):
            self.assertFalse(auto_heal._check_web_health("http://127.0.0.1:8123/openapi.json"))

    def test_restart_uses_compose_without_a_shell(self):
        with mock.patch.object(auto_heal.subprocess, "run") as run:
            run.return_value = mock.Mock(returncode=0, stderr="")
            self.assertTrue(auto_heal._restart_web())
        run.assert_called_once()
        command = run.call_args.args[0]
        self.assertEqual(command, ["docker", "compose", "restart", "web"])
        self.assertNotIn("shell", run.call_args.kwargs)


if __name__ == "__main__":
    unittest.main()
