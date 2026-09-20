import json
import threading
import unittest
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from unittest.mock import patch
import tempfile
from pathlib import Path

from control_center import server


class ControlCenterServerTests(unittest.TestCase):
    def test_snapshot_uses_read_model_then_operator_presentation(self):
        raw = {"system": {}, "portfolio": {}, "positions": [], "closed_positions": []}
        rendered = {"durum": "Sistem çalışıyor"}
        with patch.object(server, "overview", return_value=raw) as read, \
             patch.object(server, "operator_view", return_value=rendered) as present:
            self.assertEqual(server.snapshot(), rendered)
            read.assert_called_once_with()
            present.assert_called_once_with(raw)

    def test_post_is_rejected_over_http(self):
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            conn = HTTPConnection("127.0.0.1", httpd.server_address[1], timeout=2)
            conn.request("POST", "/api/v1/overview", body=b"{}",
                         headers={"Content-Type": "application/json"})
            response = conn.getresponse()
            payload = json.loads(response.read())
            self.assertEqual(response.status, 405)
            self.assertEqual(payload, {"error": "read_only"})
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=2)

    def test_static_ui_exposes_all_read_only_operator_tabs(self):
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            conn = HTTPConnection("127.0.0.1", httpd.server_address[1], timeout=2)
            conn.request("GET", "/")
            response = conn.getresponse()
            html = response.read().decode("utf-8")
            self.assertEqual(response.status, 200)
            for panel in ("openPanel", "marketPanel", "flowPanel", "riskPanel", "learningPanel", "incidentsPanel", "reportsPanel", "closedPanel"):
                self.assertIn(f'id="{panel}"', html)
                self.assertIn(f'data-tab="{panel}"', html)
            self.assertIn('fetch("/api/v1/overview"', html)
            self.assertIn("Canlı Aktivite", html)
            self.assertIn('id="activityPanel"', html)
            self.assertIn('id="activityRows"', html)
            self.assertIn("market-data-mismatch", html)
            self.assertIn('method:"POST"', html)
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=2)

    def test_only_diagnostic_feedback_post_is_allowed(self):
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            conn = HTTPConnection("127.0.0.1", httpd.server_address[1], timeout=2)
            payload = {
                "symbol": "ETHUSDT", "market_type": "USD_M_PERPETUAL",
                "operator_price": 2301.5, "agent_price": 2300.0,
            }
            with tempfile.TemporaryDirectory() as tmp, patch(
                "control_center.operator_feedback.FEEDBACK_PATH",
                Path(tmp) / "feedback.jsonl",
            ), patch.object(server, "record_data_mismatch") as record:
                record.return_value = {
                    "feedback_id": "test-id",
                    "authority": "DIAGNOSTIC_ONLY",
                    "trading_mutation_allowed": False,
                }
                conn.request(
                    "POST", "/api/v1/feedback/market-data-mismatch",
                    body=json.dumps(payload),
                    headers={"Content-Type": "application/json"},
                )
                response = conn.getresponse()
                result = json.loads(response.read())
                self.assertEqual(response.status, 201)
                self.assertEqual(result["authority"], "DIAGNOSTIC_ONLY")
                self.assertFalse(result["trading_mutation_allowed"])
                record.assert_called_once()
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=2)

    def test_v1_has_no_generic_write_surface(self):
        self.assertFalse(hasattr(server, "execute_sql"))
        self.assertFalse(hasattr(server, "mutate_database"))


if __name__ == "__main__":
    unittest.main()
