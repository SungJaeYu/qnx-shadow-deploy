#!/usr/bin/env python3
"""Serve the local workbench and stream a bounded, real C++ demo run."""
import argparse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import threading

from board_relay import BoardRelay

ROOT = Path(__file__).resolve().parents[1]
SCENARIOS = {"promote", "reject-units", "reject-delay", "reject-nan", "reject-stale",
             "reject-protocol", "reject-switch", "rollback", "baseline-failure",
             "no-fallback", "post-commit-failure", "live-deployment"}
RUN_LOCK = threading.Lock()


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        pass

    def allowed(self):
        port = self.server.server_port
        allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
        origin = self.headers.get("Origin")
        return self.headers.get("Host") in allowed_hosts and (
            origin is None or origin in {f"http://{host}" for host in allowed_hosts})

    def respond(self, status, content_type):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("Content-Security-Policy", "default-src 'self'; style-src 'self'; script-src 'self'; connect-src 'self'; frame-ancestors 'none'")
        self.end_headers()

    def do_GET(self):
        if not self.allowed():
            self.send_error(403)
            return
        if self.path == "/api/board":
            relay = self.server.board_relay
            if relay is None:
                self.send_error(404, "Board input is disabled")
                return
            if not self.server.board_observers.acquire(blocking=False):
                self.send_error(503, "Too many board observers")
                return
            try:
                self.connection.settimeout(2)
                payload = json.dumps(relay.snapshot(), ensure_ascii=True, allow_nan=False).encode()
                self.respond(200, "application/json; charset=utf-8")
                self.wfile.write(payload)
            except (BrokenPipeError, ConnectionResetError, socket.timeout):
                pass
            finally:
                self.server.board_observers.release()
            return
        files = {"/": ("index.html", "text/html; charset=utf-8"),
                 "/app.js": ("app.js", "text/javascript; charset=utf-8"),
                 "/styles.css": ("styles.css", "text/css; charset=utf-8")}
        if self.path not in files:
            self.send_error(404)
            return
        filename, content_type = files[self.path]
        self.respond(200, content_type)
        self.wfile.write((ROOT / "web" / filename).read_bytes())

    def do_POST(self):
        if not self.allowed():
            self.send_error(403)
            return
        if self.path != "/api/run":
            self.send_error(404)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if not 0 < length <= 1024 or self.headers.get_content_type() != "application/json":
                raise ValueError("Expected a small JSON request")
            payload = json.loads(self.rfile.read(length))
            scenario = payload["scenario"]
            if scenario not in SCENARIOS:
                raise ValueError("Unknown scenario")
        except (ValueError, KeyError, TypeError):
            self.send_error(400, "Invalid scenario request")
            return
        if not RUN_LOCK.acquire(blocking=False):
            self.send_error(409, "Another demonstration is running")
            return
        process = None
        timer = None
        try:
            binary = self.server.build_dir / "shadow-demo"
            worker = self.server.build_dir / "shadow-worker"
            if not binary.is_file() or not worker.is_file():
                self.send_error(503, "Build shadow-demo and shadow-worker before starting a run")
                return
            deployment = (["--candidate-manifest", str(self.server.build_dir / "candidate.manifest"),
                           "--stage-at", "20"] if scenario == "live-deployment" else ["--scenario", scenario])
            process = subprocess.Popen([str(binary), "--worker", str(worker)] + deployment
                                       + self.server.demo_options,
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                       start_new_session=True)

            def terminate_group():
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass

            timer = threading.Timer(15, terminate_group)
            timer.start()
            self.respond(200, "application/x-ndjson; charset=utf-8")
            for line in process.stdout:
                self.wfile.write(line)
                self.wfile.flush()
            code = process.wait(timeout=2)
            if code not in (0, 3):
                error = process.stderr.read(4096).decode("utf-8", errors="replace")
                self.wfile.write((json.dumps({"type": "error", "message": error or f"Demo exited with code {code}"}) + "\n").encode())
        except (BrokenPipeError, ConnectionResetError):
            pass
        finally:
            if timer:
                timer.cancel()
            if process:
                # Reap only this server's process group, including any remaining workers.
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait()
                process.stdout.close()
                process.stderr.close()
            RUN_LOCK.release()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--build-dir", type=Path, default=ROOT / "build")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--samples", type=int, default=180)
    parser.add_argument("--period-ms", type=int, default=20)
    parser.add_argument("--deadline-ms", type=int, default=10)
    parser.add_argument("--sensor-trace", type=Path, help="Read validated millimetre samples for local runs")
    parser.add_argument("--board-stdin", metavar="SOURCE", help="Relay broker JSONL from stdin under this source label")
    args = parser.parse_args()
    if args.board_stdin is not None and not (1 <= len(args.board_stdin) <= 64 and args.board_stdin.isascii()
                                            and all(c.isalnum() or c in "-_." for c in args.board_stdin)):
        parser.error("Board source must be 1..64 ASCII letters, digits, dots, underscores or hyphens")
    if not 0 < args.samples <= 1000 or not 0 < args.deadline_ms < args.period_ms <= 1000:
        parser.error("Require 1..1000 samples and 0 < deadline-ms < period-ms <= 1000")
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    server.build_dir = args.build_dir.resolve()
    server.demo_options = ["--samples", str(args.samples), "--period-ms", str(args.period_ms),
                           "--deadline-ms", str(args.deadline_ms)]
    if args.sensor_trace is not None:
        server.demo_options += ["--sensor-trace", str(args.sensor_trace.resolve())]
    server.board_relay = None
    server.board_observers = threading.BoundedSemaphore(8)
    if args.board_stdin:
        server.board_relay = BoardRelay(args.board_stdin)
        threading.Thread(target=server.board_relay.consume, args=(sys.stdin.buffer,), daemon=True).start()
    print(f"ShadowDeploy workbench: http://127.0.0.1:{server.server_port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
