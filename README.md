# 0xL0C1

> Memory lives on the object, not the app: switch phones, assistants, or models and resume at the same
> physical place.
>
> A method-of-loci agent for the physical world. No new app. Point your phone, talk, and pin what you
> learned to the object in front of you. Come back later, on any device or assistant, and pick up where
> you left off.

Built for the AI Tinkerers **"Agents, Everywhere"** Global Hackathon — Seattle, 2026-09-12.

## What it is

There is a gap between owning something and understanding it. You look up what size filter your furnace
takes, work out what MERV actually measures, and eighteen months later you run the identical search
because the chat thread that held it is gone.

0xL0C1 closes that gap by making the physical object the memory address. Three MCP tools on a remote
server talk to SQLite:

- **`observe`** — record an object, the place it lives, and any text visible on it
- **`commit`** — write a lesson, including the question you left open, only when you say to save it
- **`ask`** — resume: find the object again and return the prior thread

## Why the environment is essential

The agent lives where the object is. You do not open an app and search — you stand in front of the thing
and the thread is there. Delete the room and this product disappears; a chatbox has no furnace to walk
back to. The return visit is the whole product, and it is what a standalone chat pane cannot reproduce.

## Architecture

```
User in an assistant they already have  (no new app)
  → the assistant's vision model describes the object in words
  → remote MCP server over HTTPS  (observe | ask | commit)
  → SQLite
  → tiny local viewer
```

**We never do image recognition.** No pixels held, no embeddings computed, no OCR run. Identity arrives
as text authored by a vision model we do not control, plus what the user says. The one lever we own is
the tool schema — so `material` and `mounting` are bounded **enums** (obeyed 99.8–100% by frontier
models) rather than prose instructions (obeyed 58–78%), and `visible_verbatim_text` is **required** so
the model cannot silently drop the `20x25x1 MERV 11` printed on the filter.

## Re-identification, stated honestly

Matching is text + place + confirmation. When confidence is middling the agent asks *"did you mean the
20x25 in the upstairs return?"* rather than inventing a memory.

That is the designed behaviour, not a shortcut. Two independent research passes concluded that
vision-only instance re-identification does not work for this problem, and that unstructured
model-authored prose does not either: identical mass-produced items are 32–48% of household inventory
and their ceiling is arithmetic — `1 / N_twins`. Vision transformers are explicitly trained to discard
the micro-scuffs that could tell two identical mouldings apart, so better models are *worse* at this.
Every shipped product that tried to automate it retreated (Amazon Partpic, Sortly, Encircle, Asset
Panda, Limble).

A one-time name or place binding from the user is therefore the correct mechanism, not a failure — the
same way people distinguish two identical mugs by *"the one on my desk"* rather than by inspecting them.

**Optional short code.** For things you have committed to mastering, a printed label like `K94B` is read
by the vision model as ordinary scene text and becomes a deterministic key. Crockford Base32 excludes
`I L 1 0 O` — exactly the characters VLMs confuse. No scanner, no decoder, works on any phone.

## Run it

```bash
uv sync
uv run python server.py            # http://127.0.0.1:8130/mcp
```

Expose it over HTTPS (Tailscale, no extra tooling):

```bash
tailscale serve --bg --https=8130 http://127.0.0.1:8130     # tailnet only
tailscale funnel --bg --https=8130 http://127.0.0.1:8130    # public internet
tailscale serve --https=8130 off                            # tear down
```

Then add `https://<your-node>.ts.net:8130/mcp` as a remote MCP connector in your assistant.

## Built during the hackathon vs. brought

The rules permit "existing templates, reusable components, libraries, prompts, starter code, or other
building blocks", but require that "the project being submitted and its core functionality must be built
during the event."

**Brought** — tagged [`brought-2026-09-09`](../../releases/tag/brought-2026-09-09): this README, the
SQLite schema, the MCP server skeleton with final tool signatures returning canned stub data, and the
viewer shell. Every tool returns a `_stub` field stating it is not implemented.

**Built during the event (11:15–15:30):** the live `observe` / `ask` / `commit` implementations, the
matching engine, the confirmation path, the save gate, the carried open question, the cross-assistant
return-visit loop, and everything in this repo that implements the loop.

To see exactly which parts were created during the hackathon:

```bash
git diff brought-2026-09-09..HEAD
```

The pre-event commit is left visible in the history on purpose.

## Known limits

- **No auth.** MCP has no authentication story and we did not build one. The demo runs single-user.
- **No image handling, by design.** See above. This is a constraint, not a gap.
- **Matching is naive.** Text similarity plus place plus a confirm prompt. It will not separate two
  identical objects in the same room without a user-assigned name or a short code — and it says so
  rather than guessing.
- **Single user, single device.** A house is not owned by one person; shared memory is future work.
- **Cross-assistant support depends on the host.** Any assistant that can attach a remote MCP server
  works. Not all of them can.

## License

Apache-2.0.
