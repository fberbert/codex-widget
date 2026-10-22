import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from unittest.mock import patch

from codex_widget import claude
from codex_widget.chrome_cookies import ChromeCookie


class ClaudeWebUsageTests(unittest.TestCase):
    def test_chat_subscription_usage_loads_from_browser_session(self) -> None:
        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802
                if self.headers.get("Cookie") != "sessionKey=fixture":
                    status, data = 401, {"error": "not authenticated"}
                elif self.path == "/api/organizations":
                    status, data = 200, [
                        {"uuid": "api-only", "capabilities": None},
                        {"uuid": "chat-org", "capabilities": ["chat"],
                         "rate_limit_tier": "default_claude_max_5x"},
                    ]
                elif self.path == "/api/organizations/chat-org/usage":
                    status, data = 200, {
                        "five_hour": {"utilization": 42.0, "resets_at": "2026-10-22T03:50:00+00:00"},
                        "seven_day": {"utilization": 25.0, "resets_at": "2026-10-23T04:00:00+00:00"},
                    }
                else:
                    status, data = 403, {"error": "wrong organization"}
                body = json.dumps(data).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, format: str, *args: object) -> None:
                pass

        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            base = f"http://127.0.0.1:{server.server_port}"
            cookies = (ChromeCookie("sessionKey", "fixture", ".claude.ai"),)
            with patch.object(claude, "WEB_BASE_URL", base), \
                 patch.object(claude, "load_cookies", return_value=cookies):
                try:
                    usage = claude.load_usage(timeout=3)
                except (claude.ClaudeUsageError, TypeError) as exc:
                    self.fail(f"A sessão do navegador deveria fornecer o uso: {exc}")
            self.assertEqual(usage.five_hour.utilization, 42.0)
            self.assertEqual(usage.seven_day.utilization, 25.0)
            self.assertEqual(usage.plan, "Max 5x")
        finally:
            server.shutdown()
            thread.join(timeout=3)
            server.server_close()


if __name__ == "__main__":
    unittest.main()
