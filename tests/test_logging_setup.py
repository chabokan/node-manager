import io
import logging
import os
import tempfile
import unittest

from core import logging_setup


class LogEventTests(unittest.TestCase):
    def setUp(self):
        self.stream = io.StringIO()
        self.logger = logging.getLogger("tests.logging")
        self.logger.handlers = [logging.StreamHandler(self.stream)]
        self.logger.propagate = False
        self.logger.setLevel(logging.INFO)

    def output(self):
        return self.stream.getvalue()

    def test_log_event_is_structured_and_greppable(self):
        logging_setup.log_event(self.logger, "job_completed", name="host_command", key="abc")
        self.assertEqual(self.output().strip(),
                         "event=job_completed key=abc name=host_command")

    def test_log_event_scrubs_newlines_and_truncates(self):
        logging_setup.log_event(self.logger, "job_failed", error="line1\nline2")
        self.assertIn("error=line1 line2", self.output().splitlines()[0])
        logging_setup.log_event(self.logger, "truncated", value="x" * 500)
        self.assertLessEqual(len(self.output().splitlines()[-1]), 240)

    def test_transition_logs_only_when_the_state_changes(self):
        state = {}
        logging_setup.log_transition(state, "hub", False, self.logger, "hub_ok", "hub_failed")
        logging_setup.log_transition(state, "hub", False, self.logger, "hub_ok", "hub_failed")
        self.assertEqual(self.output().count("event="), 1)
        self.assertIn("event=hub_failed", self.output())
        logging_setup.log_transition(state, "hub", True, self.logger, "hub_ok", "hub_failed")
        self.assertEqual(self.output().count("event="), 2)
        self.assertIn("event=hub_ok", self.output())

    def test_configure_logging_writes_the_file_once(self):
        handle, path = tempfile.mkstemp(prefix="node-manager-log-")
        os.close(handle)
        os.unlink(path)
        root = logging.getLogger()
        for handler in list(root.handlers):
            if getattr(handler, logging_setup.MARKER, False):
                root.removeHandler(handler)
                handler.close()
        try:
            logging_setup.configure_logging(service="test", path=path)
            logging.getLogger("tests.probe").info("hello")
            with open(path) as stream:
                self.assertIn("service=test", stream.read())
            markers = lambda: sum(1 for handler in root.handlers
                                  if getattr(handler, logging_setup.MARKER, False))
            self.assertEqual(markers(), 2)
            logging_setup.configure_logging(service="test", path=path)
            self.assertEqual(markers(), 2)
        finally:
            for handler in list(root.handlers):
                if getattr(handler, logging_setup.MARKER, False):
                    root.removeHandler(handler)
                    handler.close()
            if os.path.exists(path):
                os.unlink(path)


if __name__ == "__main__":
    unittest.main()