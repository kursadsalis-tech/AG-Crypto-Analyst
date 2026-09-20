"""Minimal read-only HTTP surface for AGCA Live Control Center v1."""
from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from control_center.presentation import operator_view
from control_center.read_model import overview


def snapshot():
    return operator_view(overview())


class Handler(BaseHTTPRequestHandler):
    def _json(self, payload, status=200):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/api/v1/overview":
            self._json(snapshot())
            return
        if self.path == "/healthz":
            self._json({"status": "ok", "authority": "READ_ONLY"})
            return
        self._json({"error": "not_found"}, 404)

    def do_POST(self):
        # v1 starts read-only. Safe controls get an explicit allow-list later;
        # never expose a generic mutation endpoint.
        self._json({"error": "read_only"}, 405)

    def log_message(self, fmt, *args):
        return


def serve(host="127.0.0.1", port=8787):
    ThreadingHTTPServer((host, port), Handler).serve_forever()


if __name__ == "__main__":
    serve()
