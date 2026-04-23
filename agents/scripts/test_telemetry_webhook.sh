#!/usr/bin/env bash
# Tests for telemetry_webhook.py. Uses a tiny local HTTP server fixture
# to avoid hitting external services.

set -euo pipefail

PACK="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
WH="$PACK/agents/scripts/telemetry_webhook.py"
TMP="$(mktemp -d)"
trap 'rm -rf $TMP; kill $SERVER_PID 2>/dev/null || true' EXIT

pass=0; fail=0
ok() { printf "  ok    %s\n" "$1"; pass=$((pass + 1)); }
ko() { printf "  FAIL  %s\n" "$1"; fail=$((fail + 1)); }

# ---------------------------------------------------------------------------
# 1. No telemetry file -> exit 2
# ---------------------------------------------------------------------------

printf "\n=== 1: no telemetry file ===\n"
empty="$TMP/empty"
mkdir -p "$empty"
set +e
python3 "$WH" --project "$empty" --url "http://unused" >/dev/null 2>&1
rc=$?
set -e
[[ "$rc" == "2" ]] && ok "exits 2 without telemetry file" || ko "rc=$rc"

# ---------------------------------------------------------------------------
# 2. No webhook URL configured -> exit 2
# ---------------------------------------------------------------------------

printf "\n=== 2: no webhook URL configured ===\n"
proj="$TMP/proj"
mkdir -p "$proj/.cursor/telemetry"
cat > "$proj/.cursor/telemetry/agent-usage.json" <<'EOF'
{
  "started_at": "2026-01-01T00:00:00Z",
  "last_update_at": "2026-04-01T00:00:00Z",
  "summaries": {"sessions": 5, "gate_followups": 1},
  "agents": {"qa-verifier": {"invocations": 3, "last_at": "2026-04-01T00:00:00Z"}}
}
EOF

set +e
(unset AGENT_PACK_TELEMETRY_WEBHOOK; python3 "$WH" --project "$proj" >/dev/null 2>&1)
rc=$?
set -e
[[ "$rc" == "2" ]] && ok "exits 2 without URL" || ko "rc=$rc"

# ---------------------------------------------------------------------------
# 3. Dry-run renders the payload without sending
# ---------------------------------------------------------------------------

printf "\n=== 3: dry-run renders payload ===\n"
set +e
(unset AGENT_PACK_TELEMETRY_WEBHOOK; \
  python3 "$WH" --project "$proj" --dry-run >/tmp/dry.out 2>&1)
rc=$?
set -e
[[ "$rc" == "0" ]] && ok "dry-run exits 0" || ko "dry-run rc=$rc"
/usr/bin/grep -q "qa-verifier" /tmp/dry.out && ok "payload includes agent slug" || ko "agent missing"
/usr/bin/grep -q "sessions" /tmp/dry.out && ok "payload includes summary counter" || ko "summary missing"
/usr/bin/grep -q "harmonist.telemetry/v1" /tmp/dry.out && ok "schema field present" || ko "no schema field"

# ---------------------------------------------------------------------------
# 4. Live send via ephemeral local HTTP server
# ---------------------------------------------------------------------------

printf "\n=== 4: live POST to a local HTTP fixture ===\n"
python3 - <<'PY' &
import http.server, json, socketserver, os

class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a, **k): pass
    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        body = self.rfile.read(n)
        captured = {
            "body": body.decode(),
            "auth": self.headers.get("X-Pack-Token", ""),
        }
        # Persist immediately (SIGTERM may bypass any finally block).
        with open("/tmp/captured.json", "w") as f:
            json.dump(captured, f)
        self.send_response(202)
        self.send_header("Content-Type", "application/json")
        self.end_headers()
        self.wfile.write(b'{"ack": true}')

socketserver.TCPServer.allow_reuse_address = True
srv = socketserver.TCPServer(("127.0.0.1", 0), H)
print("PORT", srv.server_address[1], flush=True)
srv.serve_forever()
PY
SERVER_PID=$!
sleep 0.5

PORT=$(lsof -iTCP -sTCP:LISTEN -P -a -p $SERVER_PID 2>/dev/null | awk 'NR==2 {split($9,a,":"); print a[2]}')
if [[ -z "$PORT" ]]; then
  sleep 0.5
  PORT=$(lsof -iTCP -sTCP:LISTEN -P -a -p $SERVER_PID 2>/dev/null | awk 'NR==2 {split($9,a,":"); print a[2]}')
fi

url="http://127.0.0.1:${PORT:-0}"
set +e
out="$(python3 "$WH" --project "$proj" --url "$url" \
          --header "X-Pack-Token: secret-123" --timeout 5 2>&1)"
rc=$?
set -e
[[ "$rc" == "0" ]] && ok "POST returned 2xx" || ko "send rc=$rc: $out"
printf '%s' "$out" | /usr/bin/grep -q "delivered (202)" && ok "status 202 recognised as delivered" || ko "no delivered marker"

kill $SERVER_PID 2>/dev/null || true
wait $SERVER_PID 2>/dev/null || true

if [[ -f /tmp/captured.json ]]; then
  body="$(python3 -c 'import json; d=json.load(open("/tmp/captured.json")); print(d.get("body",""))')"
  printf '%s' "$body" | /usr/bin/grep -q '"schema": "harmonist.telemetry/v1"' \
    && ok "server captured payload with expected schema" \
    || ko "server body missing schema"
  auth="$(python3 -c 'import json; d=json.load(open("/tmp/captured.json")); print(d.get("auth",""))')"
  [[ "$auth" == "secret-123" ]] && ok "custom header forwarded" || ko "header auth='$auth'"
else
  ko "server never captured any request"
fi

# ---------------------------------------------------------------------------
# 5. Non-2xx propagates exit 1
# ---------------------------------------------------------------------------

printf "\n=== 5: non-2xx response propagates exit 1 ===\n"
python3 - <<'PY' &
import http.server, socketserver, os
PORT = int(os.environ.get("TEL_PORT", "0"))

class H(http.server.BaseHTTPRequestHandler):
    def log_message(self, *a, **k): pass
    def do_POST(self):
        n = int(self.headers.get("Content-Length", 0))
        self.rfile.read(n)
        self.send_response(500)
        self.end_headers()
        self.wfile.write(b"boom")

srv = socketserver.TCPServer(("127.0.0.1", PORT), H)
print("PORT", srv.server_address[1], flush=True)
srv.serve_forever()
PY
SERVER_PID=$!
sleep 0.5
PORT2=$(lsof -iTCP -sTCP:LISTEN -P -a -p $SERVER_PID 2>/dev/null | awk 'NR==2 {split($9,a,":"); print a[2]}')
if [[ -z "$PORT2" ]]; then
  sleep 0.5
  PORT2=$(lsof -iTCP -sTCP:LISTEN -P -a -p $SERVER_PID 2>/dev/null | awk 'NR==2 {split($9,a,":"); print a[2]}')
fi

set +e
python3 "$WH" --project "$proj" --url "http://127.0.0.1:${PORT2:-0}" --timeout 3 >/dev/null 2>&1
rc=$?
set -e
[[ "$rc" == "1" ]] && ok "500 response -> exit 1" || ko "rc=$rc"

kill $SERVER_PID 2>/dev/null || true
wait $SERVER_PID 2>/dev/null || true

echo
echo "  passed: $pass  failed: $fail"
[[ "$fail" -eq 0 ]] || exit 1
