"""Local desktop web GUI backed by the existing transactional services."""

import argparse
import json
import sqlite3
import webbrowser
from http.cookies import SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

from .application import Application, seed_demo
from .common import AccessDenied, DomainError
from .database import Repository

STATIC = Path(__file__).with_name("static")


class Handler(BaseHTTPRequestHandler):
    """Serve bundled assets and same-origin JSON requests on loopback only."""

    server_version = "Hezhang/1.1"

    def log_message(self, _format, *_args):
        """Avoid logging session tokens or potentially sensitive URLs."""

    def token(self):
        cookies = SimpleCookie()
        cookies.load(self.headers.get("Cookie", ""))
        return cookies["session"].value if "session" in cookies else None

    def send(self, status, body, mime="application/json; charset=utf-8", cookie=None):
        if not isinstance(body, bytes):
            body = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; style-src 'self' 'unsafe-inline'; "
            "script-src 'self'; connect-src 'self'; frame-ancestors 'none'",
        )
        if cookie:
            self.send_header("Set-Cookie", cookie)
        self.end_headers()
        self.wfile.write(body)

    def valid_host(self):
        return self.headers.get("Host") in {
            f"127.0.0.1:{self.server.server_port}",
            f"localhost:{self.server.server_port}",
        }

    def do_GET(self):
        """Only explicitly named assets and authenticated read endpoints exist."""
        if not self.valid_host():
            self.send(403, {"error": "拒绝未知主机"})
            return
        parsed = urlsplit(self.path)
        files = {
            "/": ("index.html", "text/html"),
            "/app.js": ("app.js", "text/javascript"),
            "/style.css": ("style.css", "text/css"),
        }
        if parsed.path in files:
            filename, mime = files[parsed.path]
            self.send(200, (STATIC / filename).read_bytes(), mime + "; charset=utf-8")
            return
        try:
            with Repository(self.server.database) as repository:
                app = Application(repository)
                params = parse_qs(parsed.query)
                if parsed.path == "/api/state":
                    result = app.state(self.token(), params.get("ledger_id", [None])[0])
                elif parsed.path == "/api/detail":
                    result = app.queries.detail(app.actor(self.token()), params["id"][0])
                elif parsed.path == "/api/expenses":
                    actor = app.actor(self.token())
                    filters = {key: values[0] for key, values in params.items()}
                    result = app.queries.expenses(actor, **filters)
                else:
                    self.send(404, {"error": "页面不存在"})
                    return
                self.send(200, result)
        except AccessDenied as exc:
            self.send(401, {"error": str(exc)})
        except (DomainError, KeyError, TypeError, StopIteration) as exc:
            self.send(400, {"error": str(exc)})

    def do_POST(self):
        """Custom header plus JSON and same-origin checks prevent cross-site writes."""
        origin = self.headers.get("Origin")
        allowed = {
            f"http://127.0.0.1:{self.server.server_port}",
            f"http://localhost:{self.server.server_port}",
        }
        if (
            not self.valid_host()
            or self.headers.get("X-Hezhang-Request") != "1"
            or (origin is not None and origin not in allowed)
        ):
            self.send(403, {"error": "拒绝跨来源请求"})
            return
        try:
            if self.headers.get_content_type() != "application/json":
                raise DomainError("请求必须使用 JSON")
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 100000:
                raise DomainError("请求大小不合法")
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict):
                raise DomainError("请求必须是对象")
            operation = self.path.removeprefix("/api/")
            with Repository(self.server.database) as repository:
                result = Application(repository).mutate(self.token(), operation, data)
            cookie = None
            if operation == "login":
                cookie = f"session={result}; HttpOnly; SameSite=Strict; Path=/; Max-Age=43200"
                result = "登录成功"
            elif operation == "logout":
                cookie = "session=; HttpOnly; SameSite=Strict; Path=/; Max-Age=0"
            self.send(200, {"result": result}, cookie=cookie)
        except AccessDenied as exc:
            self.send(403, {"error": str(exc)})
        except (DomainError, KeyError, TypeError, ValueError) as exc:
            self.send(400, {"error": str(exc)})
        except sqlite3.Error:
            self.send(503, {"error": "数据库繁忙或写入失败，请保留页面并重试"})


def main():
    """Launch the graphical application and open its local browser window."""
    parser = argparse.ArgumentParser(description="合账 · 实验二图形界面")
    parser.add_argument("--db", help="SQLite 数据库路径")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-browser", action="store_true", help="不自动打开浏览器")
    parser.add_argument(
        "--demo", action="store_true", help="使用独立演示数据库，空库时填充设计示例"
    )
    args = parser.parse_args()
    database = args.db or ("data/gui-demo.db" if args.demo else "data/hezhang.db")
    with Repository(database) as repository:
        if args.demo:
            seed_demo(repository)
    try:
        server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    except OSError as exc:
        parser.exit(1, f"启动失败：{exc}；可用 --port 8766 更换端口\n")
    server.database = database
    url = f"http://127.0.0.1:{server.server_port}"
    print(f"合账图形界面已启动：{url}\n数据库：{database}\n按 Ctrl+C 停止。", flush=True)
    if args.demo:
        print("演示用户：林晓、陈晨、周宁、许禾；密码均为 demo12345", flush=True)
    if not args.no_browser:
        webbrowser.open(url)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
