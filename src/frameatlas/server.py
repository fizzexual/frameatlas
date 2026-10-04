"""Read-only loopback viewer; routes can access only indexed dataset files."""
from __future__ import annotations

import json
import re
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from urllib.parse import parse_qs, urlsplit

from .coverage import Coverage
from .dataset import Dataset


def make_server(dataset: Dataset, port=8765):
    if type(port) is not int or not 0 <= port <= 65535:
        raise ValueError("Port must be between 0 and 65535.")
    coverage = Coverage(dataset)

    class Handler(BaseHTTPRequestHandler):
        def log_message(self, format, *args):
            pass

        def do_HEAD(self):
            self.do_GET(head=True)

        def do_GET(self, head=False):
            records = []
            try:
                allowed = {f"127.0.0.1:{self.server.server_port}", f"localhost:{self.server.server_port}"}
                origin = self.headers.get("Origin")
                if self.headers.get("Host", "").lower() not in allowed or (origin and origin not in {"http://" + host for host in allowed}) or self.headers.get("Sec-Fetch-Site") == "cross-site":
                    self.send_error(403, "Loopback viewer requires same-origin access.")
                    return
                parts = urlsplit(self.path)
                # Python 3.10 strict parsing rejects an empty query string.
                query = parse_qs(parts.query, strict_parsing=True, max_num_fields=8) if parts.query else {}
                if any(len(values) != 1 for values in query.values()):
                    raise ValueError("Duplicate query parameters are not accepted.")

                def number(name, default):
                    return int(query.get(name, [default])[0])

                def params(allowed_names):
                    if set(query) - set(allowed_names):
                        raise ValueError("Unexpected query parameters.")

                path = parts.path
                mime = "application/json; charset=utf-8"
                if path == "/":
                    params(())
                    body = files("frameatlas").joinpath("viewer.html").read_bytes()
                    mime = "text/html; charset=utf-8"
                elif path == "/api/info":
                    params(())
                    body = dataset.manifest
                elif path == "/api/frames":
                    params(("start", "count"))
                    body = dataset.frames(number("start", 0), number("count", 32))
                elif path == "/api/at-time":
                    params(("seconds",))
                    body = dataset.at_time(float(query.get("seconds", [0])[0]))
                elif path == "/api/coverage":
                    params(("session",))
                    body = coverage.report(query.get("session", ["viewer"])[0])
                elif path == "/api/sheet":
                    params(("start", "count", "columns", "cell_width"))
                    body = dataset.sheet(number("start", 0), number("count", 32), number("columns", 4), number("cell_width", 256))
                    mime = "image/png"
                elif re.fullmatch(r"/api/(image|thumbnail)/[0-9]+", path):
                    index = int(path.rsplit("/", 1)[1])
                    row = dataset.frame(index)
                    if path.startswith("/api/thumbnail/"):
                        params(())
                        body = dataset.safe_path(row["thumbnail"]).read_bytes()
                        mime = "image/jpeg"
                    else:
                        params(("width", "display"))
                        display = query.get("display", ["0"])[0]
                        if display not in {"0", "1"}:
                            raise ValueError("display must be 0 or 1.")
                        metadata, body, kind = dataset.image_bytes(index, max_width=number("width", 0) if "width" in query else None,
                                                                  display=display == "1")
                        records.append(("viewer", [index], kind))
                        mime = "image/png"
                else:
                    self.send_error(404)
                    return
                if not isinstance(body, bytes):
                    body = json.dumps(body, ensure_ascii=False, allow_nan=False).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", mime)
                self.send_header("Content-Length", str(len(body)))
                self.send_header("Cache-Control", "no-store")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header("Content-Security-Policy", "default-src 'self'; img-src 'self' blob:; style-src 'self' 'unsafe-inline'; script-src 'self' 'unsafe-inline'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
                self.end_headers()
                if not head:
                    self.wfile.write(body)
                    self.wfile.flush()
                    for session, indices, kind in records:
                        coverage.delivered(session, indices, kind)
            except (ValueError, OSError, KeyError) as error:
                if isinstance(error, (BrokenPipeError, ConnectionResetError)):
                    return
                self.send_error(400, "Invalid request or dataset integrity failure.")

    return ThreadingHTTPServer(("127.0.0.1", port), Handler)


def serve(dataset: Dataset, port=8765):
    with make_server(dataset, port) as server:
        print(f"FrameAtlas: http://127.0.0.1:{server.server_port}/", file=sys.stderr, flush=True)
        server.serve_forever()
