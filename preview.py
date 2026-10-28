"""開発用プレビュー。public/ をサーバー内(127.0.0.1)だけで配信し、VS Code のポート転送で見る。
使い方: python3 build.py && python3 preview.py  → VS Code の「ブラウザーで開く」を押す"""
import functools
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

PORT = 8060
handler = functools.partial(SimpleHTTPRequestHandler, directory=str(Path(__file__).parent / "public"))
print(f"プレビュー: http://127.0.0.1:{PORT}/ (サーバー内のみ。終了は Ctrl+C)")
ThreadingHTTPServer(("127.0.0.1", PORT), handler).serve_forever()
