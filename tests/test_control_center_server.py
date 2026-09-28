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
                "agent_bid": 2299.5, "agent_ask": 2300.5,
                "agent_quote_source": "binance", "agent_freshness": "1 s",
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
                record.assert_called_once_with(payload)
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(timeout=2)

    def test_rendered_market_mismatch_preserves_quote_evidence(self):
        import re
        import subprocess
        from functools import partial
        from html.parser import HTMLParser

        source = 'binance & "quote" <source>'
        view = server.operator_view({
            "system": {"open_positions": 0, "market_data_status": "CANLI"},
            "portfolio": {"cash": 10000, "realized_pnl": 0, "unrealized_pnl": 0},
            "positions": [], "closed_positions": [],
            "market": [{"symbol": "ETHUSDT", "market_type": "USD_M_PERPETUAL",
                        "last_price": 2300.12345678, "best_bid": 2299.12345678,
                        "best_ask": 2301.12345678, "quote_source": source,
                        "freshness_seconds": 2, "market_status": "CANLI"}],
        })
        expected = {
            "agent_price": 2300.12345678, "agent_bid": 2299.12345678,
            "agent_ask": 2301.12345678, "agent_quote_source": source,
            "agent_freshness": view["piyasa"][0]["güncellik"],
        }
        script = re.search(r"<script>(.*?)</script>",
                           server.INDEX_PATH.read_text(), re.S).group(1)
        # Execute production render/reportMismatch with a minimal synthetic DOM;
        # parse the rendered HTML attributes rather than inventing button data.
        harness = """
        const nodes = new Map();
        global.document = {
          getElementById(id) {
            if (!nodes.has(id)) nodes.set(id, {classList: {toggle() {}}});
            return nodes.get(id);
          },
          addEventListener() {}, querySelectorAll() { return []; }
        };
        global.setTimeout = () => {};
        global.fetch = () => new Promise(() => {});
        global.prompt = () => "2302.5";
        global.alert = () => {};
        """

        def run_js(tail):
            result = subprocess.run(["node", "-e", harness + script + tail],
                                    capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            return json.loads(result.stdout)

        markup = run_js("render(" + json.dumps(view) + ");" +
                        'console.log(JSON.stringify(nodes.get("marketRows").innerHTML));')

        class ButtonParser(HTMLParser):
            dataset = None

            def handle_starttag(self, tag, attrs):
                if tag == "button":
                    self.dataset = {k[5:]: v for k, v in attrs if k.startswith("data-")}

        parser = ButtonParser()
        parser.feed(markup)
        self.assertIsNotNone(parser.dataset)
        request = run_js("global.fetch = async (url, options) => {" +
                         "console.log(JSON.stringify({url, ...options})); return {ok:true};};" +
                         "reportMismatch({dataset:" + json.dumps(parser.dataset) + "});")
        payload = json.loads(request["body"])
        self.assertEqual(request["method"], "POST")
        self.assertEqual(request["url"], "/api/v1/feedback/market-data-mismatch")
        for key, value in expected.items():
            self.assertEqual(payload[key], value, key)
        with tempfile.TemporaryDirectory() as tmp, patch.object(
            server, "record_data_mismatch", wraps=partial(
                server.record_data_mismatch, path=Path(tmp) / "feedback.jsonl")
        ) as record:
            httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
            thread = threading.Thread(target=httpd.serve_forever, daemon=True)
            thread.start()
            try:
                conn = HTTPConnection("127.0.0.1", httpd.server_address[1], timeout=2)
                conn.request(request["method"], request["url"], body=request["body"].encode("utf-8"),
                             headers=request["headers"])
                response = conn.getresponse()
                response.read()
                self.assertEqual(response.status, 201)
                record.assert_called_once_with(payload)
                saved = json.loads((Path(tmp) / "feedback.jsonl").read_text())
                for key, value in expected.items():
                    self.assertEqual(saved[key], value, key)
                self.assertEqual(saved["authority"], "DIAGNOSTIC_ONLY")
                self.assertFalse(saved["trading_mutation_allowed"])
                self.assertFalse(saved["learning_mutation_allowed"])
            finally:
                httpd.shutdown()
                httpd.server_close()
                thread.join(timeout=2)

    def test_every_referenced_dom_element_exists(self):
        import re
        html = server.INDEX_PATH.read_text()
        ids = set(re.findall(r'id="([^" ]+)"', html))
        references = set(re.findall(r'\$\("([^" ]+)"\)', html))
        self.assertEqual(references - ids, set())

    def test_v1_has_no_generic_write_surface(self):
        self.assertFalse(hasattr(server, "execute_sql"))
        self.assertFalse(hasattr(server, "mutate_database"))


if __name__ == "__main__":
    unittest.main()
