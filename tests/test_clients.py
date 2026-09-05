"""Offline regression tests. No credentials or physical unit required."""

import argparse
import contextlib
import io
import json
import os
from pathlib import Path
import queue
import tempfile
import unittest
from unittest.mock import Mock, patch
import urllib.error

import bangbang
import gui
import hall_aircon as cli
import hall_aircon_api as api


class ApiTests(unittest.TestCase):
    def request(self, payload):
        response = Mock()
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        response.read.return_value = payload
        return patch.object(api.urllib.request, "urlopen", return_value=response)

    def test_body_and_authorization(self):
        with self.request(b'{"meta":{"status":200},"data":{}}') as opener:
            api.api_request("POST", "v2/ac/control", "test-token", {"power": "0"})
        request = opener.call_args.args[0]
        self.assertEqual(request.get_header("Authorization"), "Bearer test-token")
        self.assertEqual(json.loads(request.data), {"power": "0"})

    def test_api_rejection_is_not_reported_as_success(self):
        with self.request(b'{"meta":{"status":429,"message":"Slow down"}}'):
            with self.assertRaisesRegex(api.ApiError, "Slow down"):
                api.api_request("POST", "v2/ac/control")

    def test_malformed_responses(self):
        for payload in (b"<html>error</html>", b"[]", b"null", b'{"meta":[]}BAD', b'{"meta":"bad"}'):
            with self.subTest(payload=payload), self.request(payload):
                with self.assertRaises(api.ApiError):
                    api.api_request("GET", "me")

    def test_timeout(self):
        with patch.object(api.urllib.request, "urlopen", side_effect=TimeoutError("timed out")):
            with self.assertRaisesRegex(api.ApiError, "timed out"):
                api.api_request("GET", "me")

    def test_http_error_with_non_object_json(self):
        error = urllib.error.HTTPError("https://example.test", 502, "Bad Gateway", {}, io.BytesIO(b"[]"))
        with patch.object(api.urllib.request, "urlopen", side_effect=error):
            with self.assertRaises(api.ApiError) as caught:
                api.api_request("GET", "me")
        self.assertEqual(caught.exception.status, 502)

    def test_password_only_prompted_for_password_accounts(self):
        for student in (True, False):
            with self.subTest(student=student):
                responses = [{"meta": {"status": 200}, "data": {"ad_status": student}},
                             {"data": {"token": "test-token"}}]
                password = Mock(return_value="test-password")
                with patch.object(api, "api_request", side_effect=responses) as request, \
                     patch.object(api, "save_config"), patch.object(api, "load_config", return_value={}):
                    api.login("test@example.test", get_password=password,
                              get_redirect=lambda _: api.SAML_PREFIX + "test-hash")
                self.assertEqual(password.call_count, 0 if student else 1)
                self.assertEqual(request.call_args.args[1], "auth/ad/callback" if student else "auth/login")

    def test_invalid_sso_redirect_never_exchanged(self):
        with patch.object(api, "api_request", return_value={"meta": {"status": 200}, "data": {"ad_status": True}}) as request:
            with self.assertRaises(api.ApiError):
                api.login("test@example.test", get_redirect=lambda _: "https://example.test/")
        self.assertEqual(request.call_count, 1)

    def test_config_and_logout_preserve_preferences(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(api, "CONFIG_PATH", str(Path(directory) / "config.json")):
            api.save_config({"token": "test-token", "smart": {"target": 25}})
            if os.name != "nt":
                self.assertEqual(os.stat(api.CONFIG_PATH).st_mode & 0o777, 0o600)
            api.clear_token()
            self.assertEqual(api.load_config(), {"smart": {"target": 25}})
            Path(api.CONFIG_PATH).write_text("[]", encoding="utf-8")
            self.assertEqual(api.load_config(), {})

    def test_failed_config_write_preserves_previous_file(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(api, "CONFIG_PATH", str(Path(directory) / "config.json")):
            api.save_config({"token": "original"})
            with self.assertRaises(TypeError):
                api.save_config({"bad": object()})
            self.assertEqual(api.load_config(), {"token": "original"})
            self.assertEqual(len(list(Path(directory).iterdir())), 1)


class CliTests(unittest.TestCase):
    def test_invalid_temperature_is_usage_error(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as caught:
            cli.build_parser().parse_args(["temp", "warm"])
        self.assertEqual(caught.exception.code, 2)

    def test_token_precedence(self):
        with patch.object(api, "get_token", return_value="environment-token"):
            self.assertEqual(cli.resolve_token(argparse.Namespace(token="explicit-token")), "explicit-token")

    def test_empty_history_without_metadata(self):
        args = argparse.Namespace(token="test-token", limit=20, offset=0)
        with patch.object(api, "api_request", return_value={"data": []}), contextlib.redirect_stdout(io.StringIO()) as output:
            cli.cmd_usage(args)
        self.assertIn("no records", output.getvalue())


class GuiTests(unittest.TestCase):
    def test_worker_error_survives_exception_scope(self):
        for error in (api.ApiError(500, "server unavailable"), ValueError("bad value")):
            with self.subTest(error=error):
                app = Mock(_ui_queue=queue.Queue())
                handler = Mock()
                gui.App._run(app, Mock(side_effect=error), on_err=handler)
                callback = app._ui_queue.get(timeout=5)
                callback()
                handler.assert_called_once_with(str(error))

    def test_disabling_smart_cancels_pending_shutdown(self):
        app = Mock(_pending_off_id="timer", smart_target=25, smart_margin=0.3,
                   smart_off_at=24, smart_on_at=26)
        with patch.object(api, "load_config", return_value={}), patch.object(api, "save_config"):
            gui.App._set_smart(app, False)
        app.after_cancel.assert_called_once_with("timer")
        self.assertIsNone(app._pending_off_id)

    def test_stale_shutdown_does_not_control_unit(self):
        app = Mock(smart_enabled=False)
        gui.App._send_smart_off(app)
        app._send.assert_not_called()

    def test_offline_shutdown_does_not_control_unit(self):
        app = Mock(smart_enabled=True, state={"aircon": {"comm_stat": False, "power": True}})
        with patch.object(api, "get_token", return_value="test-token"):
            gui.App._send_smart_off(app)
        app._send.assert_not_called()

    def test_sparse_poll_uses_elapsed_prediction_time(self):
        app = Mock(_last_fetch_ts=100, state={"aircon": {"power": True}})
        app._smart_schedule.return_value = ("off_lead", 120)
        with patch.object(gui.time, "time", return_value=221):
            self.assertTrue(gui.App._smart_needs_poll(app))
        with patch.object(gui.time, "time", return_value=150):
            self.assertFalse(gui.App._smart_needs_poll(app))

    def test_model_does_not_schedule_offline_or_maintenance_unit(self):
        app = Mock(model={"tau_on": 3600})
        for state in ({"comm_stat": False}, {"comm_stat": True, "maintenance_mode": True}):
            self.assertIsNone(gui.App._smart_schedule(app, {"current_temperature": 25, **state}))


class ThermostatTests(unittest.TestCase):
    def controller(self, dry_run=False):
        return bangbang.Controller(argparse.Namespace(low=23, high=25, setpoint=22,
            min_on=3, min_off=4, poll=60, dry_run=dry_run, on_exit=None, rate=0.0065))

    def test_minimum_off_time_prevents_immediate_start(self):
        controller = self.controller()
        controller.fetch_state = Mock(return_value={"current_temperature": 28, "power": False, "comm_stat": True})
        controller.send = Mock()
        with contextlib.redirect_stdout(io.StringIO()):
            controller.step()
        controller.send.assert_not_called()

    def test_dry_run_never_sends_commands(self):
        controller = self.controller(dry_run=True)
        with patch.object(api, "api_request") as request:
            controller.send({"power": "1"})
        request.assert_not_called()


if __name__ == "__main__":
    unittest.main()
