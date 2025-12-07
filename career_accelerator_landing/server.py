# career_accelerator_landing/server.py
import http.server
import socketserver
import os

PORT = int(os.environ.get("PORT", "8080"))

# Serve files from this folder (career_accelerator_landing/)
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
os.chdir(BASE_DIR)

Handler = http.server.SimpleHTTPRequestHandler

with socketserver.TCPServer(("", PORT), Handler) as httpd:
    print(f"Serving landing page on port {PORT}")
    httpd.serve_forever()
