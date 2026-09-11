#!/usr/bin/env bash
# 0xL0C1 — one-command start. Prints the connector URL and leaves it running.
#
#   ./run.sh              server + tailnet-only HTTPS (safe, local testing)
#   ./run.sh --public     server + Funnel  (required for a claude.ai connector)
#   ./run.sh --stop       tear everything down
set -euo pipefail
cd "$(dirname "$0")"
PORT="${LOCI_PORT:-8130}"
TOKEN_FILE=".loci-token"

host() { tailscale status --json 2>/dev/null | python3 -c 'import json,sys; print(json.load(sys.stdin)["Self"]["DNSName"].rstrip("."))'; }

if [[ "${1:-}" == "--stop" ]]; then
  tailscale funnel --https=443 off 2>/dev/null || true
  tailscale serve --https="$PORT" off 2>/dev/null || true
  pkill -f "python server.py" 2>/dev/null || true
  echo "stopped."; exit 0
fi

[[ -f "$TOKEN_FILE" ]] || python3 -c "import secrets,string;a=string.ascii_lowercase+string.digits;print(''.join(secrets.choice(a) for _ in range(24)))" > "$TOKEN_FILE"
TOKEN=$(cat "$TOKEN_FILE")
PATHSEG="/loci-$TOKEN"

pkill -f "python server.py" 2>/dev/null || true; sleep 1
uv sync -q
nohup uv run python server.py > /tmp/loci.log 2>&1 &
for _ in $(seq 1 30); do sleep 0.5; curl -sf -o /dev/null -m 1 "http://127.0.0.1:$PORT/api/state" && break; done
curl -sf -o /dev/null -m 2 "http://127.0.0.1:$PORT/api/state" || { echo "server failed to start — see /tmp/loci.log"; tail -20 /tmp/loci.log; exit 1; }

H=$(host)
if [[ "${1:-}" == "--public" ]]; then
  # On 443. Bare non-standard ports are refused by connector backends. The app itself mounts
  # everything under /loci-<token>, so no --set-path: the whole port is proxied and /health is public.
  tailscale funnel --bg --https=443 "http://127.0.0.1:$PORT" >/dev/null
  URL="https://$H$PATHSEG"
  echo "PUBLIC.  connector URL:"
else
  tailscale serve --bg --https="$PORT" "http://127.0.0.1:$PORT" >/dev/null
  URL="https://$H:$PORT$PATHSEG"
  echo "TAILNET-ONLY (will NOT work as a claude.ai connector — use --public):"
fi
echo
echo "    $URL/mcp"
echo "    $URL/          <- viewer"
echo
echo "token is in $TOKEN_FILE (gitignored). Treat the URL as a password."
