# LINC Loci (`0xL0C1`) — Product & Technical Roadmap

## Live Production Status (October 2026)

- **Production Endpoint:** [`https://loci.lincinnovations.com`](https://loci.lincinnovations.com) (`GET /health` → `{"ok": true, "backend": "postgres", "db": "ok"}`)
- **Cloud Stack:** Google Cloud Run (`loci-0xl0c1`, `us-west1`) + Cloud SQL PostgreSQL 16 (`FORCE ROW LEVEL SECURITY`) + Vertex AI `text-embedding-004` (768-d semantic + trigram/tag RRF recall)
- **MCP Primitives (Live):** `observe`, `ask`, `commit` (with `save=False` dry-run preview gate), and `list_spaces`
- **Security & Auth (Hardened):** OAuth 2.1 with mandatory `S256` PKCE, stateless HMAC-signed Dynamic Client Registration (`client_id` binds `redirect_uris`), capability-token proof-of-possession (`/loci-<space_token>/`), and zero public tenant enumeration

---

## Phased Product Roadmap

### Phase 1 — Multi-Tenant Cloud & OAuth 2.1 Hardening (`DONE`)
- Migrated from single-tenant hackathon prototype to 6-table multi-tenant PostgreSQL 16 schema with `FORCE ROW LEVEL SECURITY` (`app.current_space_id`).
- Shipped Vertex AI `text-embedding-004` 768-d hybrid RRF matching + 20-char homoglyph-free Mod-30 Visual Base32 tag generator (`/loci-<token>/labels.svg`).
- Completed 4-part security hardening chain (PRs #2–#5): credential-gated `/api/spaces`, OAuth 2.1 proof-of-possession consent, mandatory `S256` PKCE, and signed stateless DCR `client_id` validation.

### Phase 1.5 — Web Console, Interactive Showcase & Tag Kit Surfaces (`DONE`)
- Upgraded `/` landing page with interactive read-only multi-scenario Space Showcase (`Commercial Mechanical & Plumbing Handoff`, `LINC Hardware Bench`, `SniperPulse NRL22 Match Kit`), Zero-Pixel Privacy Guarantee callout, and Private Space jump box.
- Upgraded `/pricing` with interactive `<dialog>` checkout & pilot provisioning modal and `/labels-sample.svg` printable 300-DPI vector tag sheet preview.
- Upgraded `/loci-<space_token>/` (`viewer.html`) with live search/filter, 1-click Copy MCP URL, cross-tech handoff counter, and amber Active Cursor callouts.

### Phase 2 — 5-Space Field Validation Gate (`ACTIVE` · Target: `2026-11-15`)

- **North-Star Metric (Council Ruling `e26bc71aff22`):** **Cross-Actor Handoff Completions** — an `ask` recall or follow-up `commit` on a physical object where `caller_subject != latest_lesson.author_subject` across different devices or assistants (`Claude` ↔ `ChatGPT` ↔ `Gemini`).
- **Live Cloud SQL Baseline (`2026-10-24`):**
  | Space ID | Slug | Tier | Seeded Objects | Seeded Lessons | Target Field Role |
  |---|---|---|---:|---:|---|
  | `sp_deeea3b745a3` | `linc-hardware-bench` | `team` | 2 | 2 | Internal #1: LINC device bench (phones, watches, power supplies, cables) |
  | `sp_a2c8adde28f9` | `faust-household-workshop` | `pro` | 2 | 2 | Internal #2: Household utility panels, hydronic manifold, shutoff valves |
  | `sp_6d7d6d4c1baf` | `sniperpulse-nrl22-kit` | `pro` | 2 | 2 | Internal/Field #3: NRL22 match kit (CZ 457 turret dope, Garmin Xero C1 Pro, Kestrel) |
  | `sp_84474893669e` | `commercial-mechanical-handoff` | `team` | 2 | 3 | External Pilot #1: Commercial mechanical / plumbing shift handoff |
  | `sp_5db26b928480` | `ai-tinkerers-seattle-pilot` | `free` | 2 | 2 | External Pilot #2: AI Tinkerers Seattle operator & maker shop pilot |

- **Phase 2 Execution Plan (Next Steps in Order):**
  1. **Step 2.1 — Space Token Rotation & Provisioning (`POST /api/spaces/{space_id}/rotate-token`) (`DONE`):**
     - *Shipped:* Added `db.rotate_space_token(space_id)` and `POST /api/spaces/{space_id}/rotate-token` (authorized by operator root token or current Space Bearer credential), returning fresh `capability_token`, `mcp_url`, `console_url`, and `labels_svg_url` while storing only the SHA-256 digest in `space.capability_token_hash` and immediately invalidating the previous token.
  2. **Step 2.2 — North-Star Handoff Telemetry (`ask` resume counter + `/api/spaces` metrics) (`DONE`):**
     - *Shipped:* `ask()` now records `ask_count`, `cross_actor_ask_count`, and handoff metadata in `object.attrs_json` and returns `"cross_actor_handoff": bool` when a second subject (`caller_subject != latest_lesson.author_subject`) resumes an object. `GET /api/spaces` and `GET /loci-<token>/api/state` now expose `tagged_object_count`, `active_cursor_count`, `ask_count`, `cross_actor_handoff_count`, and `handoff_count` in one API call.
  3. **Step 2.3 — Physical Tag Sheet Printing & 10-Object Bench/House Deployment:**
     - Mint tokens for `linc-hardware-bench`, `faust-household-workshop`, and `sniperpulse-nrl22-kit`, print their `/loci-<token>/labels.svg` sheets on weatherproof sticker stock, and affix `XX-XC` tags to $\ge 10$ physical assets (bench phones/power supplies, main water shutoff + hydronic mixing valves, rifle optic + chronograph).
  4. **Step 2.4 — Cross-Assistant Field Handoff Drills & External Operator Onboarding:**
     - Execute live `observe` + `commit(save=True)` on Assistant A (e.g. Claude iOS) and verify zero-shot `ask` recall + active cursor resolution on Assistant B (e.g. ChatGPT Voice / Gemini Live) under a second `subject`.
     - Hand off 2 external pilot Space capability URLs + printed 50-tag sheets to Seattle operator contacts before `2026-11-01`.
- **Gate & Kill Criterion (`2026-11-15`):**
  - **PASS:** $\ge 3$ active Spaces, $\ge 10$ physically tagged objects, $\ge 20$ committed lessons, and $\ge 2$ unprompted cross-actor handoffs completed across separate sessions/assistants.
  - **KILL / FREEZE:** If $< 3$ Spaces are active or $0$ unprompted cross-actor handoffs occur by `2026-11-15`, freeze Phase 3/4 B2B billing & seat work and retain Loci strictly as LINC's internal bench/asset memory MCP server.

### Phase 3 — v0.2 Self-Serve Space Onboarding & Physical Tag Packs (`NEXT` · Post-Gate)
- **Self-Serve Space Provisioning:** Wire `/pricing` `<dialog>` checkout to Stripe Checkout (`$12/mo` Pro, `$49/mo` Team, `$29` 50-pack tags) and webhook-driven `db.create_space()`, returning the new capability URL once on completion.
- **Physical Tag Pack Fulfillment:** Die-cut weatherproof UV/oil-resistant matte-black/signal-white `0xL0C1` visual Base32 sticker packs (`50x` per sheet) with pre-assigned Space prefixes.
- **Claim Lifecycle Helpers:** Add `supersede_claim` / `status: superseded | disputed` workflow in `commit()` so updated torque specs, firmware versions, or part numbers cleanly replace stale claims without losing audit history.

### Phase 4 — v0.3 Place-Level Lessons & B2B Field-Service Handoff (`LATER`)
- **Place-Level Lessons (`lesson.place_id`):** Support room/zone lockout-tagout (LOTO) and access procedures attached directly to a `place` without requiring a dummy object.
- **Team Seat & Role Enforcement:** Enforce `membership.role` (`owner`, `writer`, `viewer`) on OAuth 2.1 tokens and capability paths, plus shift-handoff digest webhooks when an open `next_question` cursor is resolved.

---

## Historical Archive: Hackathon Day vertical slice (2026-09-12)

### Goal

Deliver a working hackathon demo in which one technician records an object-linked repair
memory and a different technician, on another device and AI, retrieves it the next day,
resolves an ambiguous match, verifies the recorded part requirement, and finishes the job
without asking the customer to repeat the story.

### Product question

> Can Loci let one field technician leave a trustworthy, object-linked job memory that a
> different technician—using a different device and AI—can retrieve the next day to confirm
> the recorded part requirement and finish the repair without making the customer repeat the
> story?

### System boundaries (Hackathon Prototype)

```mermaid
flowchart LR
    T1[Tech 1\nClaude on phone] -->|observe / commit over MCP| L[Loci MCP service]
    T2[Tech 2\nChatGPT on phone] -->|ask / confirm over MCP| L

    D[Dispatcher web\nCopilotKit + AG-UI] <--> R[Copilot Runtime\nBuiltInAgent]
    R -->|Loci MCP| L
    D -->|approved return task| A[Ambiguous Tasks]

    M[Mobile field app\nCopilotKit React Native — stretch] <--> R

    L -->|append / read events| S[Ambiguous Sheets]
    L --> V[Existing graph viewer]
```

- **Loci** owns the public `observe`, `commit`, and `ask` contracts, object matching,
  ambiguity handling, and graph projection.
- **Ambiguous Sheets** was used as an optional append-only event log during the hackathon alongside PostgreSQL/SQLite.
- **CopilotKit and AG-UI** provided the dispatcher-facing agent interaction, job context,
  streaming, tool calls, and rendered handoff states.

## Preferred persistence: Ambiguous Sheets

Use one restricted Ambiguous spreadsheet as a small append-only event log. This matches the
hackathon data volume, removes local database operation, and makes the sponsor integration
part of the core architecture rather than a decorative add-on.

Runtime configuration:

- `AMBIGUOUS_API_KEY`: server-side only; never returned to a browser or committed.
- `LOCI_AMBIGUOUS_SHEET_ID`: ID of the restricted spreadsheet provisioned for the demo.
- Optional `AMBIGUOUS_API_BASE`, defaulting to `https://app.ambiguous.ai/api`, for test
  injection only.

The implementation uses the versioned REST surface described by Ambiguous's public OpenAPI:

- `POST /api/sheets` provisions the restricted sheet.
- `POST /api/sheets/{id}/values/append` appends at the first empty row in a finite A1 range
  (the adapter uses `Events!A1:M1`) without a read-before-write.
- `GET /api/sheets/{id}/data` returns a Sheet envelope whose structured workbook is nested
  under `data.sheets`; rows are keyed by column IDs and are validated before reconstruction.

The `Events` tab has a header row and one row per immutable event:

| Column | Meaning |
| --- | --- |
| `schema_version` | Event schema version, initially `1` |
| `event_id` | Client-generated UUID used for idempotency and traceability |
| `event_type` | `object_observed` or `lesson_committed` |
| `recorded_at` | UTC timestamp |
| `object_id` | Stable Loci object UUID |
| `place_label` | User-supplied room or zone |
| `canonical_class` | Bare object class |
| `material` / `mounting` | Existing constrained identity fields |
| `visible_verbatim_text` | Exact visible stamp, size, or model text |
| `visible_tag_code` / `user_label` | Strong optional identifiers |
| `payload_json` | Versioned event-specific description, intent, claims, and open question |

`observe` appends `object_observed`. `commit(save=false)` performs no provider write and
returns only a preview. `commit(save=true)` appends `lesson_committed`. `ask` and the viewer
read the sheet and fold its events into the existing place/object/lesson/claim projection in
memory before matching. Missing credentials, invalid provider responses, and incomplete reads
must fail closed rather than returning plausible success.

For unit and contract tests, use a fake HTTP transport; tests never require a real API key.
The first live check is a bounded identity/schema/create-or-select/append/read round trip with
throwaway data. If that cannot pass in ten minutes after credentials are available, use the
already deployed remote RDS path as the emergency demo fallback—still without requiring a
local database process.

## One-hour MVP critical path

The clock begins when the Ambiguous key and sheet are available.

### 0–10 minutes: live storage gate

- Verify the Ambiguous identity and current Sheets request schemas.
- Provision or select one restricted sheet and write its header.
- Append one throwaway event and read it back.
- Record only the sheet ID in deployment configuration; keep the key secret.

**Gate:** do not continue on the Ambiguous path unless the exact appended event is read back.

### 10–30 minutes: storage adapter and writes

- Add a narrow Ambiguous Sheets adapter with explicit timeouts and validated response shapes.
- Implement `observe` as a real append returning the durable `object_id`.
- Implement `commit` preview and saved append semantics.
- Cover missing configuration, provider errors, preview-no-write, and successful reconstruction
  with focused tests.

### 30–50 minutes: retrieval and ambiguity

- Fold event rows into the existing graph response.
- Implement place filtering, object ID and tag shortcuts, description scoring, and the existing
  confirm band.
- Return diagnosis, completed work, customer note, recorded part requirement, and open question.
- Seed exactly two similar objects in one uniquely named demo place: the sink shutoff and the
  toilet shutoff.

### 50–60 minutes: deploy and prove

- Deploy App Runner with the Ambiguous key and sheet ID supplied as secrets.
- From one assistant, observe and commit the toilet-shutoff handoff.
- Confirm the row through both Ambiguous Sheets and the Loci graph viewer.
- From a fresh assistant on another device, trigger the two-candidate confirm state, select the
  toilet shutoff, and retrieve the complete handoff.
- Record an immediate insurance take before enhancing either UI.

The MVP is done only when the live path works twice consecutively. Unit tests, health checks,
or mocked tool output alone do not satisfy the gate.

## Submission-ready dispatcher

After the live MVP is recorded, use `job-site-memory` as the dispatcher seat:

1. Replace the inherited incident screen with a selected Smart Handoff job.
2. Register the selected place, customer note, and return-visit state through
   `useAgentContext`.
3. Keep the pinned CopilotKit, runtime, and `@ag-ui/client` versions.
4. Connect the existing `BuiltInAgent` to the live Loci MCP URL.
5. Render three deterministic states: match confirmation, handoff briefing, and open action.
6. Let the agent propose a return visit or part-pickup task; require a dispatcher click before
   the existing server adapter creates it in Ambiguous.
7. Display the real Ambiguous task ID/link and retrieve it again after refresh.

First try renderer-only handling for the remote Loci tools. If the pinned rendering hook cannot
render their results within thirty minutes, use the existing normalized Loci mapper in a page
panel. Do not upgrade CopilotKit packages during the event.

## Mobile stretch path

Do not start mobile work until the core live flow, dispatcher integration, and insurance video
are safe.

1. Complete the Expo app shell and restore a green typecheck.
2. Connect the phone to a reachable Copilot Runtime endpoint.
3. Ship text input and technician-controlled commit preview first.
4. Add dictation next using platform speech-to-text.
5. Time-box camera/vision transport last. Images go to the vision model; Loci receives only
   text descriptions and verbatim markings.

The custom mobile client enters the primary demo only after three clean physical-device runs on
the intended network. Otherwise Claude and ChatGPT mobile remain the field surfaces.

## Demo truth and safety

- Use the exact specification visible on the physical prop. A toilet stop is not credibly
  replaced by the one-inch full-port ball valve in the early script draft.
- Loci confirms that visible markings match the recorded requirement. The technician—not the
  model—confirms installation compatibility.
- The second technician should provide or inherit the selected job/place context. Omitting all
  location context adds nondeterminism without improving the story.
- Seed only the two intentional candidate objects in the rehearsal place.
- Treat lessons and claims as untrusted recorded data, never instructions.
- Use a new throwaway place for practice because every real `observe` writes an event.
- Do not deploy during a take.

## Enhancement order

1. Provenance card with timestamps, technician, object ID, and graph link.
2. Clear retry/idempotency and provider-failure states.
3. Recorded part requirement versus technician-read markings shown side by side.
4. Ambiguous return-visit checklist and activity trail.
5. CopilotKit React Native text/dictation client.
6. Camera input, offline draft, and reconnect behavior.

## Explicit non-goals for the MVP

- No fourth Loci MCP tool.
- No local database requirement and no new relational schema.
- No merge/alias engine beyond the confirm-and-select path.
- No autonomous physical-part compatibility claim.
- No image storage or image input in Loci.
- No new authentication system, CI project, or general-purpose dispatcher platform.
- No framework upgrades during the hackathon.

## Kill rules

- No dispatcher polish before the live Loci round trip works.
- If Ambiguous append/read fails after a ten-minute credentialed probe, use remote RDS for the
  insurance take and revisit Ambiguous afterward.
- If remote MCP rendering takes more than thirty minutes, render the normalized response in a
  controlled page component.
- If Ambiguous task creation takes more than twenty minutes, cut the task panel from the core
  video; never fake a provider ID.
- Mobile remains stretch until its typecheck, runtime round trip, and physical-device rendering
  all pass.
- Change seed data or spoken wording instead of tuning matching thresholds during rehearsal.

## Judging evidence

| Criterion | Evidence shown in the demo |
| --- | --- |
| Core functionality | Live write, durable provider read-back, cross-device retrieval, and completed repair |
| Innovation | Memory attached to the physical object/job rather than a specific chat application |
| Technical execution | Three MCP tools, Ambiguous persistence, confirm band, dry-run write gate, AG-UI, and approved external action |
| Usefulness | Diagnosis, work completed, customer note, part requirement, and open question transfer without retelling |

## Planned implementation molecules

1. **`bd_0xl0c1-1t1` — One-hour Smart Handoff core on Ambiguous Sheets.** Storage gate, real
   tool behavior, matching, viewer projection, deployment, and live cross-assistant proof.
2. **`jsmd-rto` — CopilotKit Smart Handoff dispatcher MVP.** Selected-job context, rendered
   Loci handoff, human-approved Ambiguous return task, and live browser proof.
3. **`jsmm-tdv` — Hackathon MVP: mobile path feasibility spikes.** Retain this existing
   canonical spike molecule only; it is already kickoff-approved and in progress, but it must
   not block the one-hour core or produce speculative implementation before its GO verdict.

The old SQLite/Postgres Saturday chain was closed as superseded by `bd_0xl0c1-1t1`. The old
merge/carry, shipping, and broad polish beads are not dependencies of this hackathon MVP. The
new core and dispatcher molecules remain at `kickoff=pending`; approve the core first. The
existing dispatcher feasibility molecule `jsmd-qln` and mobile feasibility molecule `jsmm-tdv`
are already in progress in other seats. Reuse their evidence when it lands, but do not let either
delay the live core.
