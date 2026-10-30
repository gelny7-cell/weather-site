"""開発用プレビュー。public/ をサーバー内(127.0.0.1)だけで配信し、VS Code のポート転送で見る。
使い方: python3 build.py && python3 preview.py  → VS Code の「ブラウザーで開く」を押す
VS Code の転送経路は遅いため、GitHub Pages と同じく gzip で圧縮して送る"""
import gzip
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PORT = 8060
PUBLIC = Path(__file__).parent / "public"


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        name = self.path.split("?")[0].lstrip("/") or "index.html"
        path = (PUBLIC / name).resolve()
        if path.parent != PUBLIC.resolve() or not path.is_file():   # public/ 直下のファイルだけ
            self.send_error(404)
            return
        body = path.read_bytes()
        self.send_response(200)
        types = {".html": "text/html; charset=utf-8", ".png": "image/png", ".ico": "image/x-icon", ".svg": "image/svg+xml"}
        self.send_header("Content-Type", types.get(path.suffix, "application/octet-stream"))
        if "gzip" in self.headers.get("Accept-Encoding", ""):
            body = gzip.compress(body)
            self.send_header("Content-Encoding", "gzip")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


print(f"プレビュー: http://127.0.0.1:{PORT}/ (サーバー内のみ。終了は Ctrl+C)")
ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
