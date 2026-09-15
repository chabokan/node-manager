import asyncio
import datetime
import json
import os
import time
import unittest
from unittest import mock

from fastapi import FastAPI
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from api.helper import process_jobs
from api.helper import get_system_info
from api.routes import core
from core.clock import tehran_now
from crud import get_server_usages_for_period
from models import Base, ServerRootJob, ServerUsage, Setting
from server_queue import run_pending_jobs


class HubIntegrationTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://")
        Base.metadata.create_all(self.engine)
        self.db = sessionmaker(bind=self.engine)()

    def tearDown(self):
        self.db.close()
        self.engine.dispose()

    @staticmethod
    def server_info():
        return {
            "ram": {"total": 8, "used": 1},
            "cpu": {"count": 4, "usage": 1},
            "all_disk_space": 100,
            "all_disk_usage": 20,
            "disk": {},
        }

    def connect(self, response):
        response.status_code = 200
        with mock.patch.object(core, "get_system_info", return_value=self.server_info()), \
             mock.patch.object(core, "get_server_ip", return_value="127.0.0.1"), \
             mock.patch.object(core.requests, "post", return_value=response):
            return asyncio.run(core.connect("token", payload=None, db=self.db))

    def request_connect(self, body, query=b""):
        app = FastAPI()
        app.include_router(core.router, prefix="/api/v1")
        app.dependency_overrides[core.get_db] = lambda: self.db
        payload = json.dumps(body).encode()
        scope = {"type": "http", "asgi": {"version": "3.0"}, "http_version": "1.1",
                 "method": "POST", "scheme": "http", "path": "/api/v1/connect/",
                 "raw_path": b"/api/v1/connect/", "query_string": query,
                 "root_path": "", "headers": [(b"content-type", b"application/json")]}
        messages = []

        async def receive():
            return {"type": "http.request", "body": payload, "more_body": False}

        async def send(message):
            messages.append(message)

        asyncio.run(app(scope, receive, send))
        status = next(message["status"] for message in messages
                      if message["type"] == "http.response.start")
        response = json.loads(b"".join(message.get("body", b"") for message in messages
                                   if message["type"] == "http.response.body"))
        return status, response

    def test_json_body_connects_and_normalizes_https_origin(self):
        response = mock.Mock(status_code=200)
        response.json.return_value = {
            "success": True, "technical_name": "n1", "backup_server_url": "https://backup",
            "backup_server_bucket": "bucket", "backup_server_access_key": "access",
            "backup_server_secret_key": "secret"}
        with mock.patch.object(core, "get_system_info", return_value=self.server_info()), \
             mock.patch.object(core, "get_server_ip", return_value="127.0.0.1"), \
             mock.patch.object(core.requests, "post", return_value=response) as post:
            status, result = self.request_connect(
                {"token": "token", "hub_url": "https://hub.example.com/"})
        self.assertEqual(status, 200)
        self.assertTrue(result["success"])
        self.assertEqual(post.call_args.args[0],
                         "https://hub.example.com/fa/api/v1/servers/connect-server/")
        self.assertEqual(core.crud.get_setting(self.db, "base_hub_url").value,
                         "hub.example.com")

    def test_legacy_query_connect_and_repeat_are_idempotent(self):
        response = mock.Mock(status_code=200)
        response.json.return_value = {
            "success": True, "technical_name": "n1", "backup_server_url": "https://backup",
            "backup_server_bucket": "bucket", "backup_server_access_key": "access",
            "backup_server_secret_key": "secret"}
        with mock.patch.object(core, "get_system_info", return_value=self.server_info()), \
             mock.patch.object(core, "get_server_ip", return_value="127.0.0.1"), \
             mock.patch.object(core.requests, "post", return_value=response) as post:
            first = self.request_connect({}, b"token=token&hub_url=hub.example.com")
            second = self.request_connect({}, b"token=token&hub_url=hub.example.com")
        self.assertEqual(first[0], 200)
        self.assertTrue(first[1]["success"])
        self.assertTrue(second[1]["success"])
        post.assert_called_once()

    def test_invalid_hub_origin_is_rejected_before_outbound_request(self):
        with mock.patch.object(core.requests, "post") as post:
            status, result = self.request_connect(
                {"token": "token", "hub_url": "http://hub.example.com"})
        self.assertEqual(status, 422)
        post.assert_not_called()

    def test_rejected_connection_does_not_store_token(self):
        response = mock.Mock()
        response.json.return_value = {"success": False}
        self.assertFalse(self.connect(response)["success"])
        self.assertEqual(self.db.query(Setting).count(), 0)

    def test_successful_connection_stores_all_settings_together(self):
        response = mock.Mock()
        response.json.return_value = {
            "success": True, "technical_name": "n1",
            "backup_server_url": "https://backup",
            "backup_server_bucket": "bucket",
            "backup_server_access_key": "access",
            "backup_server_secret_key": "secret",
        }
        self.assertTrue(self.connect(response)["success"])
        self.assertEqual(self.db.query(Setting).count(), 7)

    def test_previous_partial_connection_can_be_recovered(self):
        self.db.add(Setting(key="token", value="token"))
        self.db.commit()
        response = mock.Mock()
        response.json.return_value = {
            "success": True, "technical_name": "n1",
            "backup_server_url": "https://backup",
            "backup_server_bucket": "bucket",
            "backup_server_access_key": "access",
            "backup_server_secret_key": "secret",
        }
        self.assertTrue(self.connect(response)["success"])
        self.assertEqual(self.db.query(Setting).count(), 7)

    def test_invalid_run_at_is_queued_and_normal_command_is_not_run_early(self):
        jobs = [{"name": "normal_command", "status": "pending",
                 "key": "job1", "data": {"command": "true"}, "run_at": "invalid"}]
        with mock.patch("api.helper.os.system") as command:
            process_jobs(self.db, jobs)
        command.assert_not_called()
        self.assertIsNotNone(self.db.query(ServerRootJob).filter_by(key="job1").first().run_at)

    def test_hub_utc_run_at_is_saved_as_naive_tehran_time(self):
        process_jobs(self.db, [{"name": "host_command", "status": "pending",
                                "key": "utc-job", "data": {"command": "true"},
                                "run_at": "2026-09-15T12:20:00Z"}])
        job = self.db.query(ServerRootJob).filter_by(key="utc-job").first()
        self.assertEqual(job.run_at, datetime.datetime(2026, 9, 15, 15, 50))

    def test_server_usage_period_filters_and_downsamples_history(self):
        now = tehran_now()
        self.db.add(ServerUsage(cpu=1, ram=1, disk=1,
                                created=now - datetime.timedelta(hours=3)))
        for minute in range(120):
            self.db.add(ServerUsage(cpu=minute + 2, ram=1, disk=1,
                                    created=now - datetime.timedelta(minutes=120 - minute)))
        self.db.commit()

        hour = get_server_usages_for_period(self.db, '1h')
        day = get_server_usages_for_period(self.db, '24h')
        self.assertTrue(hour)
        self.assertTrue(all(row.created >= now - datetime.timedelta(hours=1) for row in hour))
        self.assertTrue(any(row.cpu == 1 for row in day))
        self.assertLess(len(day), 121)

    def test_disk_payload_keeps_mountpoints_and_deduplicates_total(self):
        partitions = [mock.Mock(device='/dev/vda1', mountpoint='/'),
                      mock.Mock(device='/dev/vda1', mountpoint='/home'),
                      mock.Mock(device='/dev/vdb1', mountpoint='/storage')]
        usage = [mock.Mock(total=100 * 1024 ** 3, used=40 * 1024 ** 3,
                           free=60 * 1024 ** 3, percent=40),
                 mock.Mock(total=100 * 1024 ** 3, used=40 * 1024 ** 3,
                           free=60 * 1024 ** 3, percent=40),
                 mock.Mock(total=200 * 1024 ** 3, used=20 * 1024 ** 3,
                           free=180 * 1024 ** 3, percent=10)]
        with mock.patch('api.helper.psutil') as psutil:
            psutil.cpu_count.return_value = 4
            psutil.cpu_percent.return_value = 0
            psutil.virtual_memory.return_value = mock.Mock(total=8 * 1024 ** 3,
                available=7 * 1024 ** 3, used=1 * 1024 ** 3, free=7 * 1024 ** 3,
                percent=12.5)
            psutil.disk_partitions.return_value = partitions
            psutil.disk_usage.side_effect = usage
            info = get_system_info()
        self.assertEqual(set(info['disk']), {'/', '/home', '/storage'})
        self.assertEqual(info['disk']['/storage']['device'], '/dev/vdb1')
        self.assertEqual(info['all_disk_space'], 300)
        self.assertEqual(info['all_disk_usage'], 60)

    def test_rejected_job_fetch_is_reported(self):
        self.db.add_all([Setting(key="token", value="token"),
                         Setting(key="base_hub_url", value="hub.example")])
        self.db.commit()
        response = mock.Mock()
        response.json.return_value = {"success": False, "data": []}
        with mock.patch.object(core.requests, "post", return_value=response):
            result = asyncio.run(core.jobs(db=self.db))
        self.assertFalse(result["success"])

    def test_empty_saved_hub_url_is_repaired_without_duplicate_key(self):
        self.db.add_all([Setting(key="token", value="token"),
                         Setting(key="base_hub_url", value="")])
        self.db.commit()
        response = mock.Mock()
        response.json.return_value = {"success": True, "data": []}
        with mock.patch.object(core.requests, "post", return_value=response):
            result = asyncio.run(core.jobs(db=self.db))
        self.assertTrue(result["success"])
        self.assertEqual(self.db.query(Setting).filter_by(key="base_hub_url").count(), 1)

    def test_hub_outage_after_command_does_not_repeat_command(self):
        self.db.add(ServerRootJob(name="host_command", key="job1",
                                  data='{"command": "true"}', status="pending",
                                  run_at=datetime.datetime.now() - datetime.timedelta(seconds=1),
                                  locked=False, run_count=0))
        self.db.commit()
        with mock.patch("server_queue.os.system", return_value=0) as command, \
             mock.patch("server_queue.set_job_run_in_hub", side_effect=OSError("offline")):
            run_pending_jobs(self.db)
            run_pending_jobs(self.db)
        command.assert_called_once()
        self.assertEqual(self.db.query(ServerRootJob).filter_by(key="job1").first().status, "success")

    def test_failed_command_is_never_reported_as_success(self):
        self.db.add(ServerRootJob(name="host_command", key="job1",
                                  data='{"command": "false"}', status="pending",
                                  run_at=datetime.datetime.now() - datetime.timedelta(seconds=1),
                                  locked=False, run_count=0))
        self.db.commit()
        with mock.patch("server_queue.os.system", return_value=1), \
             mock.patch("server_queue.set_job_run_in_hub") as report:
            run_pending_jobs(self.db)
        report.assert_not_called()
        self.assertEqual(self.db.query(ServerRootJob).filter_by(key="job1").first().status, "pending")

    def test_final_failed_command_reports_safe_reason(self):
        self.db.add(ServerRootJob(name="host_command", key="job-final",
                                  data='{"command": "false"}', status="pending",
                                  run_at=datetime.datetime.now() - datetime.timedelta(seconds=1),
                                  locked=False, run_count=6))
        self.db.commit()
        with mock.patch("server_queue.os.system", return_value=1), \
             mock.patch("server_queue.set_job_run_in_hub") as report:
            run_pending_jobs(self.db)
        report.assert_called_once_with(self.db, "job-final", "failed",
                                       failure_reason="command_failed")
        self.assertEqual(self.db.query(ServerRootJob).filter_by(key="job-final").first().status,
                         "failed")

    def test_web_worker_leaves_host_update_job_for_host_cron(self):
        self.db.add(ServerRootJob(name="update_core", key="update1", data="{}",
                                  status="pending", run_at=datetime.datetime.now(),
                                  locked=False, run_count=0))
        self.db.commit()
        with mock.patch("server_queue.os.system") as command:
            run_pending_jobs(self.db, host_mode=False)
        command.assert_not_called()
        self.assertEqual(self.db.query(ServerRootJob).filter_by(key="update1").first().status,
                         "pending")

    def test_host_worker_runs_tehran_due_job_even_when_host_uses_utc(self):
        previous_tz = os.environ.get("TZ")
        try:
            os.environ["TZ"] = "UTC"
            time.tzset()
            due = tehran_now() - datetime.timedelta(minutes=1)
            self.assertGreater(due, datetime.datetime.now())
            self.db.add(ServerRootJob(name="host_command", key="tehran-due",
                                      data='{"command": "true"}', status="pending",
                                      run_at=due, locked=False, run_count=0))
            self.db.commit()
            with mock.patch("server_queue.os.system", return_value=0) as command, \
                 mock.patch("server_queue.set_job_run_in_hub"):
                run_pending_jobs(self.db)
            command.assert_called_once()
            self.assertEqual(self.db.query(ServerRootJob).filter_by(key="tehran-due").first().status,
                             "success")
        finally:
            if previous_tz is None:
                os.environ.pop("TZ", None)
            else:
                os.environ["TZ"] = previous_tz
            time.tzset()

    def test_failed_host_update_is_not_reported_as_success(self):
        data = {key: "value" for key in ("technical_name", "backup_server_url",
                                       "backup_server_access_key", "backup_server_secret_key",
                                       "backup_server_bucket")}
        self.db.add(ServerRootJob(name="update_core", key="update1",
                                  data=json.dumps(data), status="pending",
                                  run_at=datetime.datetime.now(), locked=False, run_count=0))
        self.db.commit()
        with mock.patch("server_queue.os.system", return_value=1), \
             mock.patch("server_queue.set_job_run_in_hub") as report:
            run_pending_jobs(self.db)
        report.assert_not_called()
        self.assertEqual(self.db.query(ServerRootJob).filter_by(key="update1").first().status,
                         "pending")


if __name__ == "__main__":
    unittest.main()
