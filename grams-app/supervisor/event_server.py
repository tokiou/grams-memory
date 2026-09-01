#!/usr/bin/env python3
"""Small local receiver used while validating the OpenCode plugin."""

import argparse
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class EventHandler(BaseHTTPRequestHandler):
    def do_POST(self) -> None:
        if self.path != "/events":
            self.send_error(404)
            return

        length = int(self.headers.get("content-length", "0"))
        body = self.rfile.read(length)
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            self.send_error(400, "request body must be JSON")
            return

        print(json.dumps(payload, ensure_ascii=True), flush=True)
        self.send_response(204)
        self.end_headers()

    def log_message(self, format: str, *args: object) -> None:
        return


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    server = ThreadingHTTPServer((args.host, args.port), EventHandler)
    print(f"event server listening on http://{args.host}:{args.port}/events", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
