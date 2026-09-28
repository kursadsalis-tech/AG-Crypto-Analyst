"""Minimal read-only HTTP surface for AGCA Live Control Center v1."""
from __future__ import annotations

import json
from pathlib import Path
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from control_center.presentation import operator_view
from control_center.operator_feedback import record_data_mismatch
from control_center.read_model import overview
from control_center.snapshot_cache import SnapshotCache

INDEX_PATH = Path(__file__).with_name("static") / "index.html"


def snapshot():
    return operator_view(overview())


class Handler(BaseHTTPRequestHandler):
    def _json(self, payload, status=200):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self._json_bytes(body, status)

    def _json_bytes(self, body, status=200, cache=None):
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        if cache is not None:
            self.send_header("X-AGCA-Snapshot-Status", cache["state"])
            if status == 503:
                self.send_header("Retry-After", "2")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path in {"/", "/index.html"}:
            body = INDEX_PATH.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        if self.path == "/api/v1/overview":
            body, status, cache = self.server.snapshot_cache.response()
            self._json_bytes(body, status, cache)
            return
        if self.path == "/healthz":
            self._json({"status": "ok", "authority": "READ_ONLY",
                        "snapshot_cache": self.server.snapshot_cache.telemetry()})
            return
        self._json({"error": "not_found"}, 404)

    def do_POST(self):
        if self.path != "/api/v1/feedback/market-data-mismatch":
            self._json({"error": "read_only"}, 405)
            return
        if self.headers.get("Content-Type", "").split(";")[0] != "application/json":
            self._json({"error": "json_required"}, 415)
            return
        origin = self.headers.get("Origin")
        if origin:
            from urllib.parse import urlsplit
            if urlsplit(origin).netloc != self.headers.get("Host"):
                self._json({"error": "origin_mismatch"}, 403)
                return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length <= 0 or length > 4096:
                raise ValueError("invalid_payload_size")
            payload = json.loads(self.rfile.read(length))
            if not isinstance(payload, dict):
                raise ValueError("object_required")
            record = record_data_mismatch(payload)
        except (ValueError, TypeError, json.JSONDecodeError) as exc:
            self._json({"error": str(exc)}, 400)
            return
        self._json({
            "status": "recorded",
            "feedback_id": record["feedback_id"],
            "authority": "DIAGNOSTIC_ONLY",
            "trading_mutation_allowed": False,
        }, 201)

    def log_message(self, fmt, *args):
        return


class PanelHTTPServer(ThreadingHTTPServer):
    request_queue_size = 32

    def __init__(self, address, *, cache=None):
        self.snapshot_cache = cache if cache is not None else SnapshotCache(snapshot)
        super().__init__(address, Handler)

    def server_close(self):
        self.snapshot_cache.close()
        super().server_close()


def serve(host="127.0.0.1", port=8787):
    with PanelHTTPServer((host, port)) as httpd:
        httpd.serve_forever()


if __name__ == "__main__":
    serve()
