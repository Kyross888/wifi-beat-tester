#!/usr/bin/env python3
"""
Wi-Fi Helper for the Network Tester website.

Websites cannot read your Wi-Fi name or signal. This tiny program runs on YOUR
PC, reads that info from the operating system, and lets your Vercel page ask
for it (only from the site address you give it - no other website can read it).

Run:   python wifi_helper.py https://your-app.vercel.app
       (or just  python wifi_helper.py  and type the address when asked)

Leave the window open, then open your site and click GO.
Press Ctrl+C to stop. Needs no installs (uses network_tester.py in this folder).
"""

import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

from network_tester import wifi_info

PORT = 8765


def normalize(origin):
    origin = origin.strip().rstrip("/")
    if not origin:
        return ""
    if "://" not in origin:
        origin = "https://" + origin
    p = urlparse(origin)
    return f"{p.scheme}://{p.netloc}"


def make_handler(allowed):
    class Handler(BaseHTTPRequestHandler):
        server_version = "WifiHelper"

        def log_message(self, *args):
            pass

        def cors(self):
            origin = self.headers.get("Origin", "")
            if origin in allowed:
                self.send_header("Access-Control-Allow-Origin", origin)
                self.send_header("Vary", "Origin")
                self.send_header("Access-Control-Allow-Methods", "GET, OPTIONS")
                self.send_header("Access-Control-Allow-Headers", "*")
                # lets Chrome/Edge/Brave reach a program on your own PC from a website
                self.send_header("Access-Control-Allow-Private-Network", "true")
                return True
            return False

        def do_OPTIONS(self):
            self.send_response(204 if self.cors() else 403)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def do_GET(self):
            host = self.headers.get("Host", "").rsplit(":", 1)[0]
            if host not in ("127.0.0.1", "localhost"):
                self.send_response(403)
                self.end_headers()
                return
            if urlparse(self.path).path != "/api/wifi":
                self.send_response(404)
                self.end_headers()
                return
            data = json.dumps(wifi_info()).encode("utf-8")
            self.send_response(200)
            allowed_origin = self.cors()
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            if allowed_origin:
                self.wfile.write(data)
            else:
                self.wfile.write(b"{}")  # unknown site: send nothing useful

    return Handler


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("-")]
    sites = [normalize(a) for a in args]
    if not sites:
        sites = [normalize(input("Your website address (e.g. https://my-app.vercel.app): "))]
    allowed = {s for s in sites if s}
    if not allowed:
        print("No website address given.")
        sys.exit(1)
    # also allow testing from your own PC
    allowed |= {"http://127.0.0.1:3000", "http://localhost:3000"}

    try:
        server = ThreadingHTTPServer(("127.0.0.1", PORT), make_handler(allowed))
    except OSError:
        print(f"Port {PORT} is busy - is the helper already running?")
        sys.exit(1)

    print("Wi-Fi Helper is running.")
    print("Allowed site(s): " + ", ".join(sorted(s for s in allowed if "127" not in s and "localhost" not in s)))
    print("Now open your site and click GO. Press Ctrl+C here to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopped.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
