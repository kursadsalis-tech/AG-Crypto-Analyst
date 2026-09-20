import json
import threading
import unittest
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer
from unittest.mock import patch

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
            for panel in ("openPanel", "marketPanel", "flowPanel", "riskPanel", "learningPanel", "closedPanel"):
                self.assertIn(f'id="{panel}"', html)
                self.assertIn(f'data-tab="{panel}"', html)
            self.assertIn('fetch("/api/v1/overview"', html)
            self.assertNotIn('method:"POST"', html)
            self.assertNotIn("method:'POST'", html)
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=2)

    def test_v1_has_no_generic_write_surface(self):
        self.assertFalse(hasattr(server, "execute_sql"))
        self.assertFalse(hasattr(server, "mutate_database"))


if __name__ == "__main__":
    unittest.main()
