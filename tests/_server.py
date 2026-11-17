"""A tiny local HTTP server that plays a git host's API in tests: routes map a path to (status, JSON) and every
request is recorded, so tests can check what was asked and how the token was sent."""
import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer
from urllib.parse import parse_qs, urlsplit


class FakeHost:
    def __init__(self):
        self.routes, self.requests = {}, []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                u = urlsplit(self.path)
                query = {k: v if len(v) > 1 else v[0] for k, v in parse_qs(u.query).items()}
                outer.requests.append({"path": u.path, "query": query, "auth": self.headers.get("Authorization", "")})
                route = outer.routes.get(u.path)
                status, body = route(query) if callable(route) else route if route else (404, {"message": "not found"})
                data = json.dumps(body).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, *a):
                pass

        self.server = HTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *exc):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(5)
