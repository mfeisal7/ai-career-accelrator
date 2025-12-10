import http.server
import socketserver
import os
from urllib.parse import urlparse

PORT = int(os.environ.get("PORT", "8080"))

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
os.chdir(BASE_DIR)


class ThreadingHTTPServer(socketserver.ThreadingMixIn, http.server.HTTPServer):
    """Handle each request in a separate thread."""
    daemon_threads = True
    allow_reuse_address = True


class LandingRequestHandler(http.server.SimpleHTTPRequestHandler):
    """
    Serves static landing page files and handles simple redirects.
    """

    def do_GET(self):
        parsed = urlparse(self.path)

        # Redirect /app → main web app
        if parsed.path in ("/app", "/app/"):
            self.send_response(302)
            self.send_header("Location", "https://app.aicareer.co.ke/")
            self.end_headers()
            return

        # Default: serve static files (index.html, assets, etc.)
        return super().do_GET()

    def log_message(self, format, *args):
        # Cleaner logs (optional). Comment out to restore default noisy logging.
        print(f"[{self.address_string()}] {self.log_date_time_string()} - {format % args}")


if __name__ == "__main__":
    with ThreadingHTTPServer(("", PORT), LandingRequestHandler) as httpd:
        print(f"Serving landing page on port {PORT} from {BASE_DIR}")
        httpd.serve_forever()
