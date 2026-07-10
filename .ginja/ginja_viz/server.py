"""ginja serve — tiny read-only dashboard server for the LAN.

Stdlib ThreadingHTTPServer; no new dependencies on a box running earlyoom.
All endpoints are GET-only views over state the brain already writes.
"""

import json
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse, parse_qs

from . import datafeeds

WEB_DIR = Path(__file__).parent / "web"


class Handler(BaseHTTPRequestHandler):
    server_version = "ginja-viz/0.1"

    def log_message(self, fmt, *args):  # quiet: no per-request stderr spam
        pass

    def _send_json(self, obj, status=200):
        body = json.dumps(obj).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _send_html(self, path):
        try:
            body = path.read_bytes()
        except Exception:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _sse(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        try:
            while True:
                payload = json.dumps(datafeeds.state())
                self.wfile.write(f"data: {payload}\n\n".encode())
                self.wfile.flush()
                time.sleep(2)
        except (BrokenPipeError, ConnectionResetError):
            pass

    def do_GET(self):
        url = urlparse(self.path)
        q = parse_qs(url.query)
        route = url.path.rstrip("/") or "/"

        try:
            if route == "/":
                self._send_html(WEB_DIR / "index.html")
            elif route == "/shot":
                # screenshot mode: a hidden slow image delays the load event so
                # headless browsers capture the page after data has painted
                try:
                    body = (WEB_DIR / "index.html").read_bytes().replace(
                        b'<div id="topbar">',
                        b'<img src="/api/health?sleep=6" style="display:none">'
                        b'<div id="topbar">', 1)
                except Exception:
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)
            elif route == "/api/state":
                self._send_json(datafeeds.state())
            elif route == "/api/portrait":
                self._send_json(datafeeds.portrait())
            elif route == "/api/graph":
                self._send_json(datafeeds.graph(
                    domain=(q.get("domain") or [None])[0],
                    category=(q.get("category") or [None])[0],
                    limit=int((q.get("limit") or ["600"])[0])))
            elif route == "/api/timeseries":
                self._send_json(datafeeds.timeseries(
                    hours=float((q.get("hours") or ["24"])[0])))
            elif route == "/api/evals":
                self._send_json(datafeeds.evals())
            elif route == "/api/learning":
                self._send_json(datafeeds.learning(
                    last=int((q.get("last") or ["30"])[0])))
            elif route == "/api/goals":
                self._send_json(datafeeds.goals())
            elif route == "/api/events":
                self._sse()
            elif route == "/api/health":
                # optional ?sleep=N: lets probes/screenshots wait for a warm page
                time.sleep(min(10.0, float((q.get("sleep") or ["0"])[0])))
                self._send_json({"ok": True, "ts": time.time()})
            else:
                self.send_error(404)
        except (BrokenPipeError, ConnectionResetError):
            pass
        except Exception as e:
            try:
                self._send_json({"error": str(e)[:200]}, status=500)
            except Exception:
                pass


def run(host="0.0.0.0", port=8377):
    srv = ThreadingHTTPServer((host, port), Handler)
    srv.daemon_threads = True
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()
