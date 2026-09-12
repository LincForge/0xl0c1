# 0xL0C1 — demo runbook

The current architecture is summarized in the README's [Architecture](README.md#architecture)
section. The public deployment is AWS App Runner + RDS at `loci.lincspace.ai`; exact viewer and MCP
capability URLs remain in the gitignored `.loci-cloud-url`.

## The ladder

Four rungs. Each one is already registered or already recorded before the demo starts, so falling to
the next costs seconds, not debugging.

| # | Path | Connector | Backing store |
|---|---|---|---|
| **1 — primary** | AWS App Runner + RDS, `us-west-2` | `LOCI-cloud` | RDS PostgreSQL |
| 2 — fallback | Laptop, `./run.sh --public` (Tailscale Funnel) | `LOCI-laptop` | SQLite file on the laptop |
| 3 — fallback | Claude Desktop or Claude Code against `http://127.0.0.1:8130` | local MCP config | SQLite file on the laptop |
| 4 — insurance | Play the video cut recorded at 13:45 | — | — |

**AWS is primary.** Hosting the demo on a machine at home puts the home ISP and a box you cannot
physically reach into the demo path. The cloud path is also less exposed than Funnel at a venue — no
NAT traversal, no tailnet ACL, no captive-portal interaction.

The live cloud connector URL is in the gitignored **`.loci-cloud-url`**. It is a capability URL: the
token in the path *is* the auth. Keep it out of the video frame, out of screenshots, and out of the
repo.

Rung 3 loses nothing that matters: the room is still the environment, only the chat surface moves.

## Rungs 1 and 2 do not share a database

The cloud holds RDS; the laptop holds a SQLite file. They share nothing — which is the point, a failure
in one cannot take out the other, but it also means **the whole observe → commit → ask sequence must run
on one rung**. If you fall from 1 to 2 mid-demo, the graph you built does not follow you. Fall over
between takes, not inside one.

## Before Saturday — test off your own network

Tether to your phone's cellular, turn wifi **off**, and run the full loop from the phone against the
cloud connector (`LOCI-cloud`). Then do the same against `LOCI-laptop`. This is the only way to catch:

- venue wifi blocking outbound, captive portals, and NAT behaviour that differs from home
- whether Funnel is actually enabled for the laptop's node in the tailnet ACL (only the mini has used it)
- that the connector still lists tools when the phone is on a carrier network, not just at the desk

Untested infrastructure is worse than tested flaky infrastructure. Anything stood up on Friday is
untested by definition until this runs.

## Known-good verification

```bash
curl -s -X POST "$URL/mcp" \
  -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"t","version":"1"}}}'
```

Expect `"serverInfo":{"name":"0xL0C1"}`. Then `tools/list` should return `observe`, `ask`, `commit`.
`$URL` is the full capability base — `https://<host>/loci-<token>` — for either rung.

Also check `GET /health` (unauthenticated, no token needed): it should report
`{"ok": true, "backend": "postgres", "db": "ok"}` on the cloud, `"backend": "sqlite"` on the laptop.
`backend: postgres` with `db: error` means App Runner cannot reach RDS — that is a security-group
problem, not a code problem. Drop to rung 2.

## Gotchas paid for already

- **Mount on 443 under a path.** A bare non-standard port (`:8130`) never handed a tool list to the
  connector; `443/loci-<token>` works. Same shape as the Enroy connector on the mini.
- **The token in the path is the auth.** Claude connectors cannot attach arbitrary static headers, so
  a capability URL is the practical control. Keep it out of the video frame, the repo, and screenshots.
- **Do not push an image during a recorded take.** App Runner has `AutoDeploymentsEnabled` — a push to
  ECR redeploys the service within about three minutes, mid-sentence if you are unlucky. Restarts no
  longer invalidate open MCP sessions (the server runs `stateless_http=True`, so live connectors survive
  a redeploy), but the instance is still unreachable for part of the rollover. Freeze pushes during
  takes; push freely between them.
- **`tailscale serve status --json` before touching serve config.** The mini hosts six other entries
  (`8102/8103/8108/8111/8119` + `/enroy`) that a careless command can clobber.
- **The graph is shared and open.** One memory space for everyone holding the URL. Do not observe
  anything you would mind a room reading, and expect that someone will try the prompt-injection beat
  on you — which is fine, that is rehearsed (claims come back as data, never instructions).

- **TLS lags the custom-domain `active` status by ~1 minute.** Right after `loci.lincspace.ai` goes
  active, curl can still see the `*.awsapprunner.com` wildcard cert (exit 60). It clears on its own.
  If it ever reappears, use the awsapprunner URL from `.loci-cloud-url` — same service, same token.
