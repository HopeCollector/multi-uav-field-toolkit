"""Host-side UDP monitor aggregator and local Web UI server."""

from __future__ import annotations

import argparse
import ipaddress
import json
import mimetypes
import socket
import threading
import time
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from multi_uav_field_toolkit.monitor.common.protocol import (
    DEFAULT_IMAGE_PORT,
    DEFAULT_STATUS_PORT,
    DEFAULT_WEB_PORT,
    ProtocolError,
)
from multi_uav_field_toolkit.monitor.host.aggregator import MonitorAggregator

WEB_ROOT = Path(__file__).resolve().parents[1] / "web"


class UdpListener(threading.Thread):
    def __init__(self, *, bind: str, port: int, kind: str, aggregator: MonitorAggregator) -> None:
        super().__init__(daemon=True)
        self.bind = bind
        self.port = port
        self.kind = kind
        self.aggregator = aggregator
        self.stop_event = threading.Event()
        self.socket: socket.socket | None = None

    def run(self) -> None:
        sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind((self.bind, self.port))
        sock.settimeout(0.5)
        self.socket = sock
        while not self.stop_event.is_set():
            try:
                packet, source = sock.recvfrom(65535)
            except TimeoutError:
                continue
            except OSError:
                break
            try:
                if self.kind == "status":
                    self.aggregator.ingest_status_packet(packet, source)
                else:
                    self.aggregator.ingest_image_packet(packet, source)
            except ProtocolError:
                continue

    def stop(self) -> None:
        self.stop_event.set()
        if self.socket is not None:
            self.socket.close()


class MonitorHttpHandler(BaseHTTPRequestHandler):
    server_version = "MultiUavMonitor/0.1"

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/snapshot":
            self._send_json(self.server.aggregator.snapshot())  # type: ignore[attr-defined]
            return
        if parsed.path == "/api/events":
            self._send_events()
            return
        if parsed.path.startswith("/api/image/"):
            self._send_image(parsed.path.removeprefix("/api/image/"))
            return
        if parsed.path.startswith("/api/mjpeg/"):
            self._send_mjpeg(parsed.path.removeprefix("/api/mjpeg/"))
            return
        if parsed.path == "/":
            self._send_static(WEB_ROOT / "index.html")
            return
        static_path = (WEB_ROOT / parsed.path.lstrip("/")).resolve()
        if WEB_ROOT in static_path.parents and static_path.is_file():
            self._send_static(static_path)
            return
        self.send_error(HTTPStatus.NOT_FOUND, "not found")

    def log_message(self, format: str, *args: object) -> None:
        if getattr(self.server, "quiet", False):  # type: ignore[attr-defined]
            return
        super().log_message(format, *args)

    def _send_json(self, value: object) -> None:
        body = json.dumps(value, ensure_ascii=False).encode("utf-8")
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_events(self) -> None:
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "text/event-stream; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        try:
            while True:
                payload = json.dumps(self.server.aggregator.snapshot(), ensure_ascii=False)  # type: ignore[attr-defined]
                self.wfile.write(f"data: {payload}\n\n".encode())
                self.wfile.flush()
                time.sleep(1.0)
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            return

    def _send_static(self, path: Path) -> None:
        body = path.read_bytes()
        content_type = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", content_type)
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _send_image(self, uav_id: str) -> None:
        image = self.server.aggregator.latest_image(uav_id)  # type: ignore[attr-defined]
        if image is None:
            self.send_response(HTTPStatus.NO_CONTENT)
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            return
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", "image/jpeg")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(image)))
        self.end_headers()
        self.wfile.write(image)

    def _send_mjpeg(self, uav_id: str) -> None:
        boundary = "uav-monitor-frame"
        self.send_response(HTTPStatus.OK)
        self.send_header("Content-Type", f"multipart/x-mixed-replace; boundary={boundary}")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Connection", "keep-alive")
        self.end_headers()
        version = -1
        try:
            while True:
                image, version = self.server.aggregator.wait_for_image(uav_id, version)  # type: ignore[attr-defined]
                if image is None:
                    time.sleep(0.1)
                    continue
                self.wfile.write(f"--{boundary}\r\n".encode("ascii"))
                self.wfile.write(b"Content-Type: image/jpeg\r\n")
                self.wfile.write(f"Content-Length: {len(image)}\r\n\r\n".encode("ascii"))
                self.wfile.write(image)
                self.wfile.write(b"\r\n")
                self.wfile.flush()
        except (BrokenPipeError, ConnectionResetError, TimeoutError):
            return


class MonitorHttpServer(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(
        self,
        server_address: tuple[str, int],
        aggregator: MonitorAggregator,
        quiet: bool = False,
    ) -> None:
        super().__init__(server_address, MonitorHttpHandler)
        self.aggregator = aggregator
        self.quiet = quiet


def is_loopback_bind(bind: str) -> bool:
    if bind.lower() == "localhost":
        return True
    try:
        return ipaddress.ip_address(bind).is_loopback
    except ValueError:
        return False


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run host-side multi-UAV monitor WebUI.")
    parser.add_argument("--bind", default="127.0.0.1", help="UDP and HTTP bind address.")
    parser.add_argument(
        "--allow-remote",
        action="store_true",
        help="Allow a non-loopback bind. The monitor has no authentication.",
    )
    parser.add_argument("--web-port", type=int, default=DEFAULT_WEB_PORT)
    parser.add_argument("--status-port", type=int, default=DEFAULT_STATUS_PORT)
    parser.add_argument("--image-port", type=int, default=DEFAULT_IMAGE_PORT)
    parser.add_argument("--uav", action="append", default=None, help="Expected UAV id.")
    parser.add_argument("--quiet", action="store_true", help="Suppress HTTP request logs.")
    args = parser.parse_args(argv)
    if not is_loopback_bind(args.bind) and not args.allow_remote:
        parser.error(
            "non-loopback --bind requires --allow-remote; the monitor has no authentication"
        )
    return args


def main() -> None:
    args = parse_args()
    expected_uavs = args.uav or ["uav1", "uav2", "uav3"]
    aggregator = MonitorAggregator(expected_uavs=expected_uavs)
    status_listener = UdpListener(
        bind=args.bind, port=args.status_port, kind="status", aggregator=aggregator
    )
    image_listener = UdpListener(
        bind=args.bind, port=args.image_port, kind="image", aggregator=aggregator
    )
    status_listener.start()
    image_listener.start()
    httpd = MonitorHttpServer((args.bind, args.web_port), aggregator=aggregator, quiet=args.quiet)
    if not is_loopback_bind(args.bind):
        print("WARNING: remote clients can read unauthenticated telemetry and images.")
    display_host = "127.0.0.1" if args.bind in {"0.0.0.0", "localhost"} else args.bind
    print(f"WebUI: http://{display_host}:{args.web_port}")
    print(f"UDP status: {args.bind}:{args.status_port}")
    print(f"UDP image:  {args.bind}:{args.image_port}")
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("Stopping monitor.")
    finally:
        status_listener.stop()
        image_listener.stop()
        httpd.server_close()


if __name__ == "__main__":
    main()
