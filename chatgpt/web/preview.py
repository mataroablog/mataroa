"""Local browser tests and fictional preview; no database or Node required."""

import argparse
from contextlib import suppress
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from django.template import Context, Engine

WEB = Path(__file__).resolve().parent
ROOT = WEB.parents[1]
ASSETS = ROOT / "main/static/mcp"
TEMPLATE = Engine(dirs=[ROOT / "main/templates"]).get_template("main/mcp_posts.html")
FILES = {
    "/tests/": WEB / "tests/index.html",
    "/preview/": WEB / "preview.html",
    "/fixture-host.js": WEB / "fixture-host.js",
    **{f"/tests/{file.name}": file for file in (WEB / "tests").glob("*.js")},
    **{
        f"/static/mcp/{file.name}": file
        for file in ASSETS.iterdir()
        if file.suffix in {".js", ".css"}
    },
}


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        path = urlsplit(self.path).path
        origin = f"http://127.0.0.1:{self.server.server_port}"
        csp = None
        if path == "/":
            self.send_response(302)
            self.send_header("Location", "/tests/")
            self.end_headers()
            return
        if path in {"/posts.html", "/unit.html", "/opaque.html"}:
            assets = {
                name: f"{origin}/static/mcp/{filename}"
                for name, filename in {
                    "style": "posts.css",
                    "model": "model.js",
                    "posts": "posts.js",
                    "bridge": "bridge.js",
                    "main": "main.js",
                }.items()
            }
            html = TEMPLATE.render(Context({"assets": assets}))
            if path == "/unit.html":
                html = html.replace(
                    f'<script defer src="{assets["main"]}"></script>', ""
                )
            if path == "/opaque.html":
                html = html.replace(
                    "</body>", '<script defer src="/tests/opaque.js"></script></body>'
                )
            body = html.encode()
            mime = "text/html"
            csp = f"default-src 'none'; script-src {origin}; style-src {origin} 'unsafe-inline'; connect-src 'none'; base-uri 'none'; form-action 'none'; img-src data:"
        elif path == "/bridge.html":
            body = b'<!doctype html><html><body><script src="/static/mcp/bridge.js"></script></body></html>'
            mime = "text/html"
        elif path in FILES:
            file = FILES[path]
            body = file.read_bytes()
            mime = {".js": "text/javascript", ".css": "text/css", ".html": "text/html"}[
                file.suffix
            ]
        else:
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", mime + "; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        if csp:
            self.send_header("Content-Security-Policy", csp)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format, *args):
        if args[1] != "200":
            super().log_message(format, *args)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=4173)
    args = parser.parse_args()
    with ThreadingHTTPServer(("127.0.0.1", args.port), Handler) as server:
        print(
            f"Browser tests: http://127.0.0.1:{server.server_port}/tests/", flush=True
        )
        print(
            f"Fictional preview: http://127.0.0.1:{server.server_port}/preview/",
            flush=True,
        )
        with suppress(KeyboardInterrupt):
            server.serve_forever()
