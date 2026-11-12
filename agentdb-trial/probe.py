import json
import os
import sys
import urllib.error
import urllib.request

ENDPOINT = os.environ.get("AGENTDB_ENDPOINT", "")
TOKEN_PATH = "/var/run/secrets/agentdb/token"
TIMEOUT = 30


class Client:
    def __init__(self):
        self.token = None
        if os.path.exists(TOKEN_PATH):
            with open(TOKEN_PATH) as f:
                self.token = f.read().strip()
        self.session = None
        self.next_id = 1

    def _post(self, body):
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        }
        if self.token:
            headers["Authorization"] = "Bearer " + self.token
        if self.session:
            headers["Mcp-Session-Id"] = self.session
        req = urllib.request.Request(
            ENDPOINT, data=json.dumps(body).encode(), headers=headers, method="POST"
        )
        try:
            resp = urllib.request.urlopen(req, timeout=TIMEOUT)
        except urllib.error.HTTPError as e:
            raise RuntimeError("HTTP %d: %s" % (e.code, e.read().decode("utf-8", "replace")[:500]))
        with resp:
            sid = resp.headers.get("Mcp-Session-Id")
            if sid:
                self.session = sid
            ctype = resp.headers.get("Content-Type", "")
            raw = resp.read().decode("utf-8", "replace")
        return ctype, raw

    @staticmethod
    def _parse(ctype, raw, want_id):
        if "text/event-stream" in ctype:
            for line in raw.splitlines():
                if not line.startswith("data:"):
                    continue
                try:
                    msg = json.loads(line[5:].strip())
                except ValueError:
                    continue
                if isinstance(msg, dict) and msg.get("id") == want_id:
                    return msg
            raise RuntimeError("no response with id %s in event stream" % want_id)
        msg = json.loads(raw)
        if isinstance(msg, list):
            for m in msg:
                if m.get("id") == want_id:
                    return m
            raise RuntimeError("no response with id %s in batch" % want_id)
        return msg

    def request(self, method, params):
        rid = self.next_id
        self.next_id += 1
        ctype, raw = self._post({"jsonrpc": "2.0", "id": rid, "method": method, "params": params})
        msg = self._parse(ctype, raw, rid)
        if "error" in msg:
            raise RuntimeError("JSON-RPC error: %s" % json.dumps(msg["error"]))
        return msg.get("result", {})

    def initialize(self):
        self.request("initialize", {
            "protocolVersion": "2025-06-18",
            "capabilities": {},
            "clientInfo": {"name": "agentdb-probe", "version": "1"},
        })
        self._post({"jsonrpc": "2.0", "method": "notifications/initialized"})

    def call(self, name, arguments=None, summary=None):
        try:
            result = self.request("tools/call", {"name": name, "arguments": arguments or {}})
            is_error = bool(result.get("isError"))
            text = "\n".join(c.get("text", "") for c in result.get("content", []) if isinstance(c, dict))
        except RuntimeError as e:
            is_error, text = True, str(e)
        print(json.dumps({
            "tool": name,
            "arguments": summary if summary is not None else arguments,
            "isError": is_error,
            "result": text[:2000],
        }), flush=True)
        return is_error, text


def structural(c, name, args):
    for confirm in (False, True):
        a = dict(args, confirm=confirm)
        err, text = c.call(name, a, summary={"confirm": confirm, **{k: v for k, v in args.items() if k not in ("purpose", "future_use", "other_identities")}})
        if err:
            if "already exists" in text:
                return
            raise RuntimeError("%s failed: %s" % (name, text[:300]))


def setup(c):
    err, text = c.call("whoami")
    if err:
        raise RuntimeError("whoami failed")
    common = {
        "purpose": "Trial probe database to verify agentdb works end to end.",
        "future_use": "Read by the probe check job to confirm a second caller sees it.",
        "other_identities": "None; access control is off on this trial, so every caller can read it.",
    }
    structural(c, "create_database", dict(common, database="probe"))
    structural(c, "create_table", dict(common, database="probe", table="notes",
                                       columns=[{"name": "body", "type": "text", "nullable": True}]))


def check(c):
    err, text = c.call("whoami")
    if err:
        raise RuntimeError("whoami failed")
    err, text = c.call("list_tables")
    if err:
        raise RuntimeError("list_tables failed")
    if "probe" not in text or "notes" not in text:
        raise RuntimeError("list_tables does not include probe.notes")


def main():
    if len(sys.argv) != 2 or sys.argv[1] not in ("setup", "check"):
        print("usage: probe.py setup|check", file=sys.stderr)
        return 2
    if not ENDPOINT:
        print("AGENTDB_ENDPOINT not set", file=sys.stderr)
        return 2
    try:
        c = Client()
        c.initialize()
        (setup if sys.argv[1] == "setup" else check)(c)
    except Exception as e:
        print(json.dumps({"fatal": str(e)}), flush=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
