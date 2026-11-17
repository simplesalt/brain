"""Local fake of the Effort MCP server for hook tests (127.0.0.1, stateless JSON-RPC)."""
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class FakeEffortServer:
    def __init__(self):
        self.efforts = {}
        self.calls = []
        self.mode = "ok"
        self.fail_refs = set()
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                length = int(self.headers.get("content-length") or 0)
                body = json.loads(self.rfile.read(length))
                params = body.get("params") or {}
                name = params.get("name")
                args = params.get("arguments") or {}
                outer.calls.append((name, args))
                if outer.mode == "sleep":
                    threading.Event().wait(5)
                if outer.mode == "http500":
                    self.send_response(500)
                    self.end_headers()
                    return
                if outer.mode == "garbage":
                    text, error = "this is not json", False
                elif outer.mode == "fail" or args.get("ref") in outer.fail_refs:
                    text, error = "error: failure", True
                elif name == "effort_get":
                    data = outer.efforts.get(args.get("ref"))
                    if data is None:
                        text, error = "not found", True
                    else:
                        text, error = json.dumps(data, indent=2), False
                else:
                    text, error = json.dumps({"ref": args.get("ref")}), False
                result = {"content": [{"type": "text", "text": text}]}
                if error:
                    result["isError"] = True
                out = json.dumps({"jsonrpc": "2.0", "id": body.get("id"), "result": result}).encode()
                self.send_response(200)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(out)))
                self.end_headers()
                self.wfile.write(out)

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = "http://127.0.0.1:%d/mcp/coordinator" % self.httpd.server_address[1]
        self.httpd.daemon_threads = True
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)

    def start(self):
        self.thread.start()

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()
