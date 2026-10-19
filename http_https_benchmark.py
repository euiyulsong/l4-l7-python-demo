"""Compare HTTP and HTTPS request latency against the same local Python server.

Run: python3 http_https_benchmark.py --requests 100 --warmup 10
Only Python's standard library is used. A local self-signed certificate is
generated with OpenSSL when one is not already present in this directory.
"""

import argparse
import json
import math
import platform
import statistics
import subprocess
import ssl
import threading
import time
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

HERE = Path(__file__).resolve().parent
CERT, KEY = HERE / "cert.pem", HERE / "key.pem"
BODY = b"HTTP and HTTPS use the same Python handler and response payload.\n"


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_GET(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Content-Length", str(len(BODY)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(BODY)
        self.close_connection = True

    def log_message(self, fmt, *args):
        pass


def ensure_certificate():
    if CERT.exists() and KEY.exists():
        return
    subprocess.run(
        [
            "openssl", "req", "-x509", "-newkey", "rsa:2048", "-nodes",
            "-keyout", str(KEY), "-out", str(CERT), "-days", "7",
            "-subj", "/CN=localhost", "-addext", "subjectAltName=DNS:localhost,IP:127.0.0.1",
        ],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def percentile(values, percent):
    ordered = sorted(values)
    index = max(0, math.ceil(percent * len(ordered)) - 1)
    return ordered[index]


def request_once(url, context=None):
    request = urllib.request.Request(url, headers={"Connection": "close"})
    start = time.perf_counter()
    with urllib.request.urlopen(request, timeout=5, context=context) as response:
        body = response.read()
        status = response.status
    return (time.perf_counter() - start) * 1000, status, body


def measure(url, count, warmup, context):
    for _ in range(warmup):
        request_once(url, context)
    samples = []
    statuses = set()
    sizes = set()
    for _ in range(count):
        elapsed, status, body = request_once(url, context)
        samples.append(elapsed)
        statuses.add(status)
        sizes.add(len(body))
    return {
        "requests": count,
        "status_codes": sorted(statuses),
        "response_bytes": sorted(sizes),
        "latency_ms": {
            "mean": round(statistics.mean(samples), 3),
            "median": round(statistics.median(samples), 3),
            "p95": round(percentile(samples, 0.95), 3),
            "min": round(min(samples), 3),
            "max": round(max(samples), 3),
        },
    }


def start_server(port, https=False):
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    server.daemon_threads = True
    if https:
        tls = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        tls.load_cert_chain(CERT, KEY)
        server.socket = tls.wrap_socket(server.socket, server_side=True)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--requests", type=int, default=100, help="measured requests per protocol")
    parser.add_argument("--warmup", type=int, default=10, help="unmeasured requests per protocol")
    args = parser.parse_args()
    if args.requests < 1 or args.warmup < 0:
        parser.error("--requests must be >= 1 and --warmup must be >= 0")

    ensure_certificate()
    servers = [start_server(9080), start_server(9444, https=True)]
    try:
        # Trust this experiment's local certificate without disabling TLS.
        context = ssl.create_default_context(cafile=str(CERT))
        context.check_hostname = False
        results = {
            "experiment": "local Python HTTP vs HTTPS",
            "method": "sequential requests; each request opens a new connection; warmup excluded",
            "python": platform.python_version(),
            "requests_per_protocol": args.requests,
            "warmup_per_protocol": args.warmup,
            "http": measure("http://127.0.0.1:9080/", args.requests, args.warmup, None),
            "https": measure("https://127.0.0.1:9444/", args.requests, args.warmup, context),
        }
        http_mean = results["http"]["latency_ms"]["mean"]
        https_mean = results["https"]["latency_ms"]["mean"]
        results["https_mean_over_http_percent"] = round((https_mean / http_mean - 1) * 100, 2) if http_mean else None
        print(json.dumps(results, indent=2, ensure_ascii=False))
    finally:
        for server in servers:
            server.shutdown()
            server.server_close()


if __name__ == "__main__":
    main()
