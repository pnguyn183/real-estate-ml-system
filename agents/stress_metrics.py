"""Metrics for the bounded stress producer, with a fixed scenario label set."""
from __future__ import annotations

from prometheus_client import CollectorRegistry, Counter, Gauge, generate_latest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread


class StressMetrics:
    def __init__(self) -> None:
        self.registry = CollectorRegistry()
        self.generated = Counter("stress_generated_messages_total", "Generated messages", ["scenario"], registry=self.registry)
        self.delivered = Counter("stress_delivered_messages_total", "Broker-acknowledged messages", ["scenario"], registry=self.registry)
        self.failed = Counter("stress_failed_messages_total", "Terminal publication failures", ["reason"], registry=self.registry)
        self.duplicates = Counter("stress_duplicate_messages_total", "Generated repeat URL messages", registry=self.registry)
        self.unstructured = Counter("stress_unstructured_messages_total", "Generated text-first messages", registry=self.registry)
        self.active = Gauge("stress_run_active", "One while a bounded run is active", registry=self.registry)
        self.burst = Gauge("stress_burst_active", "One during the configured burst", registry=self.registry)
        self.rate = Gauge("stress_delivery_rate_per_second", "Average broker acknowledgments per elapsed run second", registry=self.registry)
        self.duration = Gauge("stress_run_duration_seconds", "Elapsed time of current or last run", registry=self.registry)

    def serve(self, port: int) -> ThreadingHTTPServer:
        registry = self.registry

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                if self.path == "/health":
                    body, content_type = b'{"status":"ok"}', "application/json"
                elif self.path == "/metrics":
                    body, content_type = generate_latest(registry), "text/plain; version=0.0.4; charset=utf-8"
                else:
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_args):
                pass

        server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
        Thread(target=server.serve_forever, name="stress-health", daemon=True).start()
        return server
