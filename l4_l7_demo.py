"""One-file L4 TCP passthrough vs L7 HTTPS routing demo.

Prerequisite: openssl executable in PATH (used only to generate a local TLS cert).
Run: python3 l4_l7_demo.py
Test: curl -k https://localhost:8443/users
      curl -k https://localhost:8443/orders
      curl -k https://localhost:9443/users
      curl -k https://localhost:9443/orders
"""

import http.client
import select
import socket
import socketserver
import ssl
import subprocess
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
CERT, KEY = HERE / "cert.pem", HERE / "key.pem"


def ensure_certificate():
    if CERT.exists() and KEY.exists():
        return
    print("Generating a self-signed TLS certificate for localhost...")
    subprocess.run(
        [
            "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
            "-keyout", str(KEY), "-out", str(CERT), "-days", "7",
            "-subj", "/CN=localhost", "-addext", "subjectAltName=DNS:localhost",
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def tls_context():
    context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    context.load_cert_chain(certfile=CERT, keyfile=KEY)
    return context


class Backend(BaseHTTPRequestHandler):
    def do_GET(self):
        body = f"backend={self.server.label}, path={self.path}\n".encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/plain")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        print(f"[{self.server.label}] {fmt % args}", flush=True)


class L4Tunnel(socketserver.BaseRequestHandler):
    def handle(self):
        print("[L4] TCP connection: forwarding encrypted bytes (no HTTP inspection)", flush=True)
        with socket.create_connection(("127.0.0.1", 9001)) as backend:
            sockets = [self.request, backend]
            while True:
                readable, _, _ = select.select(sockets, [], [])
                for source in readable:
                    data = source.recv(65536)
                    if not data:
                        return
                    target = backend if source is self.request else self.request
                    target.sendall(data)


class TCPServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


class L7Gateway(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path.startswith("/users"):
            port, name = 9002, "USERS"
        elif self.path.startswith("/orders"):
            port, name = 9003, "ORDERS"
        else:
            self.send_error(404, "No matching route")
            return

        print(f"[L7] Decrypted HTTP GET {self.path} -> {name} backend", flush=True)
        conn = http.client.HTTPConnection("127.0.0.1", port, timeout=5)
        try:
            conn.request("GET", self.path)
            response = conn.getresponse()
            body = response.read()
            self.send_response(response.status)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        finally:
            conn.close()

    def log_message(self, fmt, *args):
        pass


def start_http(port, handler, label=None, tls=False):
    server = ThreadingHTTPServer(("127.0.0.1", port), handler)
    if label:
        server.label = label
    if tls:
        server.socket = tls_context().wrap_socket(server.socket, server_side=True)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def main():
    ensure_certificate()
    start_http(9001, Backend, label="TLS_BACKEND", tls=True)
    start_http(9002, Backend, label="USERS")
    start_http(9003, Backend, label="ORDERS")
    tcp_proxy = TCPServer(("127.0.0.1", 8443), L4Tunnel)
    threading.Thread(target=tcp_proxy.serve_forever, daemon=True).start()
    start_http(9443, L7Gateway, tls=True)
    print("L4 passthrough : https://localhost:8443 (TLS terminates at backend:9001)")
    print("L7 TLS + path : https://localhost:9443 (TLS terminates at proxy:9443)")
    print("Run curl -k against /users and /orders on each port. Ctrl+C to stop.")
    threading.Event().wait()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nStopped")
    except FileNotFoundError as exc:
        if exc.filename == "openssl":
            raise SystemExit("OpenSSL not found. Install OpenSSL or create cert.pem/key.pem manually.") from exc
        raise
