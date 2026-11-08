#!/usr/bin/env python3
"""Bearer-token gate in front of a local OpenAI-compatible model server (Ollama / vLLM), standard library only.

Ollama has no authentication, so it must never face the internet directly. This proxy accepts only the two routes the CQA
judge uses, requires `Authorization: Bearer <token>` on both, and forwards to the model server on localhost. Put a
cloudflared tunnel in front of it and give the hosted API `LOCAL_ENDPOINT=https://<tunnel>/v1` and `LOCAL_API_KEY=<token>`.

  CQA_PROXY_TOKEN=<long random> CQA_PROXY_PORT=11437 CQA_UPSTREAM=http://127.0.0.1:11436 python3 token_proxy.py

Cloudflare closes a connection when the origin sends nothing for about 100 s, and one judge call on a 27B model can take
longer. So the proxy answers at once (200, chunked) and sends a space every 15 s while the model works; JSON allows leading
whitespace, so the client parses the body as usual. An upstream failure after that point is reported as a JSON object
with an "error" key (the judge treats it as a failed call). Replies are not streamed (the judge does not stream).
"""
from __future__ import annotations

import hmac
import http.server
import json
import os
import socketserver
import sys
import threading
import time
import urllib.error
import urllib.request

TOKEN = os.environ.get("CQA_PROXY_TOKEN", "")
PORT = int(os.environ.get("CQA_PROXY_PORT", "11437"))
UPSTREAM = os.environ.get("CQA_UPSTREAM", "http://127.0.0.1:11436").rstrip("/")
UPSTREAM_TIMEOUT = float(os.environ.get("CQA_UPSTREAM_TIMEOUT", "1200"))
HEARTBEAT_S = float(os.environ.get("CQA_HEARTBEAT_S", "15"))
ALLOWED = {("GET", "/v1/models"), ("POST", "/v1/chat/completions")}
MAX_BODY = 4 * 1024 * 1024

if len(TOKEN) < 24:
    sys.exit("CQA_PROXY_TOKEN must be set (at least 24 characters)")


def upstream(method: str, path: str, body: bytes | None) -> tuple[int, bytes]:
    req = urllib.request.Request(UPSTREAM + path, data=body, method=method,
                                 headers={"Content-Type": "application/json", "Accept": "application/json"})
    try:
        with urllib.request.urlopen(req, timeout=UPSTREAM_TIMEOUT) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read() or json.dumps({"error": {"message": f"upstream HTTP {e.code}"}}).encode()
    except Exception as e:  # upstream down or timed out
        return 502, json.dumps({"error": {"message": f"upstream {type(e).__name__}: {e}"}}).encode()


class Handler(http.server.BaseHTTPRequestHandler):
    server_version = "cqa-token-proxy/2"
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt, *args):  # one line per request, no bodies
        sys.stderr.write(f"[{time.strftime('%H:%M:%S')}] {self.client_address[0]} {fmt % args}\n")

    def _authorized(self) -> bool:
        auth = self.headers.get("Authorization", "")
        return auth.startswith("Bearer ") and hmac.compare_digest(auth[7:].strip(), TOKEN)

    def _reply(self, status: int, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(body)

    def _chunk(self, data: bytes) -> None:
        self.wfile.write(f"{len(data):x}\r\n".encode() + data + b"\r\n")
        self.wfile.flush()

    def _handle(self, method: str) -> None:
        path = self.path.split("?", 1)[0]
        if (method, path) not in ALLOWED:
            return self._reply(404, b'{"error":{"message":"not found"}}')
        if not self._authorized():
            return self._reply(401, b'{"error":{"message":"unauthorized"}}')
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_BODY:
            return self._reply(413, b'{"error":{"message":"body too large"}}')
        body = self.rfile.read(length) if length else None
        if method == "GET":
            status, data = upstream(method, path, None)
            return self._reply(status, data)
        try:
            payload = json.loads(body or b"")
            payload["stream"] = False  # the gate does not relay streams
            body = json.dumps(payload).encode("utf-8")
        except (ValueError, TypeError, AttributeError):
            return self._reply(400, b'{"error":{"message":"invalid json"}}')

        result: dict = {}
        worker = threading.Thread(target=lambda: result.update(dict(zip(("status", "data"), upstream(method, path, body)))), daemon=True)
        worker.start()
        worker.join(timeout=2.0)  # fast answers keep their real status code
        if not worker.is_alive():
            return self._reply(result["status"], result["data"])
        # long call: commit to 200 + chunked now, keep the edge alive with whitespace, then send the JSON body
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Transfer-Encoding", "chunked")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.flush()
        try:
            while worker.is_alive():
                worker.join(timeout=HEARTBEAT_S)
                if worker.is_alive():
                    self._chunk(b" ")
            data = result["data"]
            if result["status"] != 200:
                try:
                    err = json.loads(data)
                    data = json.dumps({"error": err.get("error", err) if isinstance(err, dict) else err, "upstream_status": result["status"]}).encode()
                except ValueError:
                    data = json.dumps({"error": {"message": data.decode("utf-8", "replace")[:500]}, "upstream_status": result["status"]}).encode()
            self._chunk(data)
            self.wfile.write(b"0\r\n\r\n")
            self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError):
            pass  # the client left; the upstream call finishes on its own

    def do_GET(self):
        self._handle("GET")

    def do_POST(self):
        self._handle("POST")


class Server(socketserver.ThreadingMixIn, http.server.HTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def server_bind(self):  # skip HTTPServer's reverse DNS lookup of the bind address (seconds on some hosts)
        socketserver.TCPServer.server_bind(self)
        self.server_name, self.server_port = "127.0.0.1", self.server_address[1]


if __name__ == "__main__":
    print(f"token proxy on 127.0.0.1:{PORT} -> {UPSTREAM} (routes: {sorted(ALLOWED)}, heartbeat {HEARTBEAT_S:.0f} s)", flush=True)
    Server(("127.0.0.1", PORT), Handler).serve_forever()
