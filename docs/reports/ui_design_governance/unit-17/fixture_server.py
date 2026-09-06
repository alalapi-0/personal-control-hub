#!/usr/bin/env python3
"""Serve three pre-copied TC17 sources and authored synthetic data on loopback.

Usage: python3 fixture_server.py --source-dir /path/to/disposable/copies
The source directory must contain copies, never point this at an external project.
No directory listing, filesystem fallback, command execution or write API exists.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import stat
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit

from synthetic_data import GET_JSON, MARKDOWN, PROGRESS, ROUNDS

SOURCE_TYPES = {
    "progress.html": "text/html; charset=utf-8",
    "progress_ui.css": "text/css; charset=utf-8",
    "progress_ui.js": "text/javascript; charset=utf-8",
}
WRAPPER = """<!doctype html><html lang="zh-CN"><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>学习计划 · 隔离界面观察</title><style>
html,body{margin:0;height:100%;font:14px/1.5 system-ui;background:#fff;color:#182c36}
body{display:flex;flex-direction:column}header{padding:10px 16px;background:#fff5d9;border-bottom:1px solid #c9b97d}
header strong{display:block}iframe{display:block;width:100%;flex:1;border:0;min-height:0}
</style><header><strong>真实界面代码 · 合成演示数据</strong>
仅供隔离观察：记录、完成、存档与命令执行请求均返回 405；不会写入真实项目。
界面的临时偏好可能保存在本浏览器的独立端口中。</header>
<iframe title="学习计划真实界面与合成示例" src="/progress.html"></iframe></html>"""


def json_bytes(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def load_routes(source_dir):
    """Read exactly the three regular files, through a pinned no-follow directory."""
    directory = Path(source_dir)
    # Reject symlink components except the OS's conventional /tmp alias.
    for component in (directory, *directory.parents):
        if component.is_symlink() and str(component) != "/tmp":
            raise ValueError("source directory must not contain symlink components")
    directory_fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    routes = {
        "/": ("text/html; charset=utf-8", WRAPPER.encode("utf-8")),
        # Inert transparent SVG, synthetic and never a file read.
        "/favicon.ico": ("image/svg+xml", b'<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 16 16"></svg>'),
    }
    hashes = {}
    try:
        for name, mime in SOURCE_TYPES.items():
            fd = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=directory_fd)
            with os.fdopen(fd, "rb") as stream:
                if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                    raise ValueError("source must be a regular file")
                payload = stream.read()
            routes["/" + name] = (mime, payload)
            hashes[name] = hashlib.sha256(payload).hexdigest()
    finally:
        os.close(directory_fd)
    for path, value in GET_JSON.items():
        routes[path] = ("application/json; charset=utf-8", json_bytes(value))
    for path, value in MARKDOWN.items():
        routes[path] = ("text/markdown; charset=utf-8", value.encode("utf-8"))
    for name, variable, value in (("progress_data.js", "PROGRESS_DATA", PROGRESS), ("rounds_data.js", "ROUNDS_DATA", ROUNDS)):
        routes["/" + name] = ("text/javascript; charset=utf-8", b"window." + variable.encode() + b"=" + json_bytes(value) + b";\n")
    return routes, hashes


def handler_for(routes):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass  # Avoid storing arbitrary request text or browser query values.

        def respond(self, status, mime, body):
            self.send_response(status)
            self.send_header("Content-Type", mime)
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Cache-Control", "no-store")
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Content-Security-Policy", "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; frame-src 'self'; frame-ancestors 'self'; object-src 'none'; base-uri 'none'; form-action 'none'")
            if status == 405:
                self.send_header("Allow", "GET, HEAD")
            self.end_headers()
            if self.command != "HEAD":
                self.wfile.write(body)

        def do_GET(self):
            expected_host = f"127.0.0.1:{self.server.server_port}"
            if self.headers.get("Host") != expected_host:
                return self.respond(403, "application/json", b'{"error":"host_not_allowed"}')
            # BaseHTTPRequestHandler normalizes leading //; validate raw target first.
            raw_target = self.requestline.split()[1]
            try:
                target = urlsplit(raw_target)
            except ValueError:
                return self.respond(400, "application/json", b'{"error":"invalid_target"}')
            if target.scheme or target.netloc or target.fragment or not raw_target.startswith("/") or raw_target.startswith("//"):
                return self.respond(404, "application/json", b'{"error":"not_allowlisted"}')
            # Exact path lookup: encoded traversal, aliases and arbitrary files all fail.
            route = routes.get(target.path)
            if route is None:
                return self.respond(404, "application/json", b'{"error":"not_allowlisted"}')
            self.respond(200, *route)

        do_HEAD = do_GET

        def reject_method(self):
            self.close_connection = True
            self.respond(405, "application/json; charset=utf-8", json_bytes({"error": "隔离合成演示：写入与命令执行已停用。"}))

        def __getattr__(self, name):
            if name.startswith("do_"):
                return self.reject_method
            raise AttributeError(name)

    return Handler


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-dir", required=True, type=Path)
    args = parser.parse_args()
    routes, hashes = load_routes(args.source_dir.absolute())
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler_for(routes))
    print(json.dumps({"url": f"http://127.0.0.1:{server.server_port}/", "source_sha256": hashes, "synthetic": True, "read_only": True}, ensure_ascii=False), flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
