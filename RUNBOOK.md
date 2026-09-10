# 0xL0C1 — demo runbook

## Host on the laptop, not the mini

The demo machine must be the laptop you have with you. Hosting on the Mac mini puts your home
ISP and a machine you cannot physically reach into the demo path — three fragile links, and the
fix for two of them is a twenty-minute drive.

Running on the laptop also preserves the pitch: *"the database is a file on my laptop."* That line
is the entire privacy story and the reason there is no OAuth. Cloud hosting kills it.

```bash
./run.sh --public      # start + Funnel, prints the connector URL
./run.sh --stop        # tear down
```

## Register BOTH machines as connectors

Free redundancy. Run `./run.sh --public` on the laptop *and* leave the mini's endpoint up, then add
both to claude.ai as separate connectors (`LOCI-laptop`, `LOCI-mini`). If one path dies mid-demo you
switch connectors instead of debugging. They share nothing, so a failure in one cannot take out the other.

Note the two have **different SQLite files**. Whichever machine you demo on is the one holding the
graph — do the whole observe → commit → ask sequence on one host.

## Before Saturday — test off your own network

Tether to your phone's cellular and run the full loop. This is the only way to catch:

- whether Funnel is enabled for the laptop's node in the tailnet ACL (only the mini has used it)
- captive portals and blocked outbound on foreign wifi
- NAT behaviour that differs from home

Untested infrastructure is worse than tested flaky infrastructure. Anything stood up on Friday is
untested by definition.

## If the connector dies mid-demo

1. Switch to the other registered connector. Costs seconds.
2. Still dead → fall back to a client that needs no public path at all: Claude Desktop or Claude Code
   against `http://127.0.0.1:8130/mcp` on the laptop. The environment story is unchanged — the room is
   still the environment — only the chat surface moves.
3. Still dead → play the insurance video cut recorded at 13:45.

## Known-good verification

```bash
curl -s -X POST "$URL/mcp" \
  -H 'Content-Type: application/json' -H 'Accept: application/json, text/event-stream' \
  -d '{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2025-06-18","capabilities":{},"clientInfo":{"name":"t","version":"1"}}}'
```

Expect `"serverInfo":{"name":"0xL0C1"}`. Then `tools/list` should return `observe`, `ask`, `commit`.

## Gotchas paid for already

- **Mount on 443 under a path.** A bare non-standard port (`:8130`) never handed a tool list to the
  connector; `443/loci-<token>` works. Same shape as the Enroy connector on the mini.
- **The token in the path is the auth.** Claude connectors cannot attach arbitrary static headers, so
  a capability URL is the practical control. Keep it out of the video frame, the repo, and screenshots.
- **Restarting the server invalidates open MCP sessions.** Do not restart mid-demo.
- **`tailscale serve status --json` before touching serve config.** The mini hosts six other entries
  (`8102/8103/8108/8111/8119` + `/enroy`) that a careless command can clobber.
