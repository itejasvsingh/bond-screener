"""Vercel serverless endpoint for grounded bond research chat."""
import json
import sys
from http.server import BaseHTTPRequestHandler
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import rag  # noqa: E402

SNAPSHOT = Path(__file__).resolve().parent / "data"


@lru_cache(maxsize=1)
def load_data():
    """Load the snapshot bundled alongside the function or in the workspace."""
    bonds = []
    about = {}
    for bond_path in (SNAPSHOT / "bonds.json", ROOT / "dist" / "bonds.json", ROOT / "data" / "bonds.json"):
        if bond_path.is_file():
            try:
                with bond_path.open(encoding="utf-8") as bond_file:
                    bonds = json.load(bond_file).get("bonds", [])
                break
            except (OSError, ValueError):
                pass

    for about_path in (SNAPSHOT / "about.json", ROOT / "dist" / "about.json", ROOT / "about.json"):
        if about_path.is_file():
            try:
                with about_path.open(encoding="utf-8") as about_file:
                    about = json.load(about_file).get("about", {})
                break
            except (OSError, ValueError):
                pass

    return bonds, about


def run_chat(question):
    bonds, about = load_data()
    return rag.answer(question, bonds, about)


class handler(BaseHTTPRequestHandler):
    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_POST(self):
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._respond(400, {"error": "Invalid Content-Length"})
            return
        if length <= 0 or length > 16_384:
            self._respond(413 if length > 16_384 else 400, {"error": "Invalid request size"})
            return
        try:
            request = json.loads(self.rfile.read(length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._respond(400, {"error": "Request body must be valid JSON"})
            return
        if not isinstance(request, dict):
            self._respond(400, {"error": "Request body must be a JSON object"})
            return
        try:
            result = run_chat(request.get("question"))
        except rag.ChatError as error:
            self._respond(400, {"error": str(error)})
        except rag.ProviderError as error:
            self._respond(502, {"error": str(error)})
        except (OSError, ValueError, TypeError) as error:
            self._respond(500, {"error": "The bond snapshot could not be loaded for chat."})
        else:
            self._respond(200, result)

    def _respond(self, status, payload):
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *_):
        pass
