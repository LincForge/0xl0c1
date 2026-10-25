# LINC Loci (`0xL0C1`) — Product & Business Roadmap

## 1. Live Production Status (October 2026)

- **Production Endpoint:** [`https://loci.lincinnovations.com`](https://loci.lincinnovations.com) (`GET /health` → `{"ok": true, "backend": "postgres", "db": "ok"}`)
- **Cloud Stack:** Google Cloud Run (`loci-0xl0c1`, `us-west1`, revision `loci-00014-gx2`) + Cloud SQL PostgreSQL 16 (`FORCE ROW LEVEL SECURITY`) + Vertex AI `text-embedding-004` (768-d semantic + trigram/tag RRF recall)
- **MCP Primitives (Live):** `observe`, `ask`, `commit` (with `save=False` dry-run preview gate), and `list_spaces`
- **Security & Auth (Hardened):** OAuth 2.1 with mandatory `S256` PKCE, stateless HMAC-signed Dynamic Client Registration (`client_id` binds `redirect_uris`), capability-token proof-of-possession (`/loci-<space_token>/`), and zero public tenant enumeration (`176 passed` in `just check`)

---

## 2. Joint Product & Business Council Ruling (`2026-10-24`)

- **Product Council (`session_id: bd162c8d31cc`):** Marty Cagan, Teresa Torres, Shreyas Doshi, Ryan Singer, Eliyahu Goldratt
- **Business LINC Council (`session_id: 48c4186c0bc4`):** Peter Thiel, Jeff Bezos, Patrick Collison, Patrick McKenzie (`patio11`), Seth Godin, Naval Ravikant

### Core Council Decisions (11/11 Unanimous)

1. **Honest Baseline Accounting (Drill vs. External Unprompted Handoffs):**
   - Internal dogfooding and synthetic cross-assistant drills (`14` drill handoffs across `15` tagged objects on `2026-10-24`) prove the technical plumbing works, **not** market demand.
   - **Strict North-Star Definition:** An **External Unprompted Cross-Actor Handoff** requires:
     1. A non-LINC human at an external organization,
     2. On an object physically tagged in their workspace,
     3. Where `caller_subject != latest_lesson.author_subject` (or a zero-install capability viewer/QR recall by a second person) with **zero prompting from LINC in the prior 24 hours**.
   - **Current Honest External Baseline (`2026-10-24`):** `0` external unprompted handoffs, `$0` external revenue.
2. **True System Bottleneck (Goldratt / Torres / Collison):**
   - The constraint is **physical stickers on real equipment in outsiders' hands** and **Person #2's setup friction** (configuring a custom OAuth MCP connector on a phone).
   - **Zero-Install Handoff Path:** The read-only capability URL (`/loci-<viewer_token>/`) and QR/short-link scan on the tag sheet allow Technician #2 to read the active cursor and part specs in 1 second **without** installing an MCP connector first.
3. **Go-To-Market Wedge — Two-Track Validation (No Second Funnel):**
   - **Track A — Embedded Memory Layer for LINC HIL Bench (`Lane A` Bundle, 5/6 Business Council):** Every paid **$49 Watch+Phone Bench Run** and **$7,500 Device Truth Audit** ships with a pre-provisioned LOCI Space bound to the device under test (`CR-GT`, `C9-R7`). Zero additional CAC; tests whether AI-native hardware/mobile teams retain and query the Space after their bench run.
   - **Track B — Commercial Mechanical / Field-Service Pilot (Godin + All 11 Advisors):** Hand-deliver 10 pre-bound weatherproof stickers to a named commercial mechanical/plumbing operator (`sp_84474893669e`), observe a $\le 15$-minute uncoached setup, and quote a site-based price (**`$149/mo per site/shop` + `$300` setup**, not `$4.90/seat`).
4. **Minimum Viable Correction & UI Scope (Appetite: Max 1 Weekend):**
   - **No self-serve Stripe webhook provisioning, seat/role matrices, or bulk CRUD admin panels before Milestone M4.** Use a 10-minute **Stripe Payment Link** with manual `POST /api/spaces` fulfillment for the first 10 buyers.
   - **Trust-Preserving Corrections Only:** Pull `supersede_claim` / soft-retract forward in MCP and add a minimal 3-action console correction bar (**Resolve `next_question` cursor**, **Edit object label/place**, **Soft-retract lesson**) guarded by a separate write confirmation/token and tagged `actor=console:human` so cleanup edits never pollute handoff telemetry.
5. **Time & Attention Cap:**
   - Cap standalone LOCI work at **$\le 4\text{ hours/week}$** until **M2 (`2026-11-15`)** and **M3 (`2026-12-06`)** pass so LOCI reinforces rather than competes with LINC's `2027-01-31` Lane A revenue gate.

---

## 3. 90-Day Milestone Roadmap (`M1` → `M4` · Oct 24, 2026 – Jan 22, 2027)

| Milestone | Target Date | Concrete Deliverables | Hard PASS Gate | Hard KILL / FREEZE Rule |
|---|---|---|---|---|
| **M1 — Physical Stickers, VLM Readability & Payment Link** | **`2026-11-01`** | 1. Print `.env.print-bundle/*.svg` on weatherproof stock and affix all 12 internal tags (`linc-hardware-bench`, `faust-household-workshop`, `sniperpulse-nrl22-kit`).<br>2. Run **20-photo VLM Tag Readability Benchmark** (glare, grease, 45° angle, 4 ft distance across Claude/ChatGPT/Gemini).<br>3. Put physical stickers in **2 named outside pilot operators' hands** during an observed $\le 15$-min uncoached setup.<br>4. Add live **Stripe Payment Links** (`$29` Tag Kit / `$149/mo` Site Pilot) on `/pricing` with manual fulfillment. | • VLM tag read hit rate $\ge 90\%$ ($18/20$)<br>• $\ge 2$ outside operators connect & complete 1st `observe` + `commit` uncoached within 15 min<br>• Live Stripe Payment Link verified | • If VLM readability $< 80\%$, add compact QR fallback to `labels.py` immediately.<br>• If an outside pilot cannot complete MCP setup in 15 min, default Tech #2 to zero-install Web Viewer / QR handoff. |
| **M2 — Honest External Field Validation Gate** | **`2026-11-15`** | 1. Exclude internal/drill subjects (`drill=true`, `@lincinnovations.com`, `@lincforge.com`, `@sniperpulse.com`) from external gate count.<br>2. Ship `supersede_claim` + console **Resolve Cursor / Edit Label / Soft-Retract** (`actor=console:human`) with separate viewer vs. writer token.<br>3. Conduct $\ge 2$ recorded 15-min story interviews with pilot operators. | • $\ge 2$ external Spaces with $\ge 3$ physically tagged objects each (photo proof)<br>• **$\ge 2$ unprompted external cross-actor handoffs** across distinct humans/sessions in Cloud SQL logs | **0 external unprompted handoffs on `2026-11-15`** → **FREEZE** all standalone LOCI SaaS/UI/billing work. Retain LOCI strictly as LINC's internal bench memory MCP and embedded Lane A artifact. |
| **M3 — First External Dollar & Repeat Weekly Pull** | **`2026-12-06`** | 1. Present priced site pilot (`$149/mo` per site/shop or `$250` 60-day pilot + tag kit) to $\ge 5$ qualified prospects (device/HIL labs + mechanical contractors).<br>2. Attach a pre-provisioned LOCI Space to every Lane A bench run deliverable. | • $\ge 1$ external Space records unprompted handoffs in **2 consecutive weeks** without LINC nudging<br>• **$\ge 1$ non-friend external buyer pays real money** ($\ge \$29$ tag kit or $\ge \$99/\text{mo}$ pilot) via Stripe Payment Link | **$0$ paid commitments after $\ge 5$ qualified asks** → Hide public SaaS pricing grid behind *"Included with LINC Bench Runs"* and stop standalone LOCI sales. |
| **M4 — Retention, Lane A Attach & Self-Serve Decision** | **`2027-01-22`** *(9 days before Lane A `01-31` gate)* | 1. Evaluate 30-day retention, second-month billing renewal, and Lane A Space attach rate.<br>2. **Go / No-Go Decision Memo** on Phase 3 self-serve automation. | • **$\ge 3$ paying external Spaces** ($\ge \$150\text{–}\$300$ MRR)<br>• $\ge 1$ second-month renewal<br>• $\ge 2$ Lane A bench runs delivered with an active attached LOCI Space | **Below threshold** → Permanently internalize LOCI as LINC lab infrastructure; do **not** build automated Stripe webhooks, seat/role matrices, or standalone marketing. |

---

## 4. Current Cloud SQL Telemetry Snapshot (`2026-10-24`)

> **Accounting Note (Council Ruling `bd162c8d31cc` / `48c4186c0bc4`):** Rows 1–3 are internal LINC dogfood Spaces; the `14` cross-actor handoffs recorded on `2026-10-24` are synthetic verification drills (`drill=true`). External unprompted handoff baseline for the **M2 (`2026-11-15`)** gate starts at **`0`**.

| Space ID | Slug | Scope | Tier | Tagged Objects | Committed Lessons | Active Cursors | Drill Handoffs | External Unprompted Handoffs |
|---|---|---|---|---:|---:|---:|---:|---:|
| `sp_deeea3b745a3` | `linc-hardware-bench` | Internal Dogfood #1 | `team` | 4 (`CP-V7`, `CK-CD`, `CR-GT`, `C9-R7`) | 5 | 3 | 5 | — |
| `sp_a2c8adde28f9` | `faust-household-workshop` | Internal Dogfood #2 | `pro` | 4 (`FA-A5`, `FA-HR`, `FA-Z2`, `FA-CK`) | 5 | 3 | 4 | — |
| `sp_6d7d6d4c1baf` | `sniperpulse-nrl22-kit` | Internal Dogfood #3 | `pro` | 4 (`SP-X4`, `SP-MZ`, `SP-2S`, `SP-QP`) | 5 | 3 | 4 | — |
| `sp_84474893669e` | `commercial-mechanical-handoff` | **External Pilot #1** | `team` | 2 (`CM-A2`, `CM-PT`) | 3 | 1 | 1 | **0** *(Target: $\ge 1$ by Nov 15)* |
| `sp_5db26b928480` | `ai-tinkerers-seattle-pilot` | **External Pilot #2** | `free` | 1 (`AT-2P`) | 2 | 2 | 0 | **0** *(Target: $\ge 1$ by Nov 15)* |
| **Totals** | | **3 Internal · 2 External** | | **15** | **20** | **12** | **14** | **0 / 2 Required for M2** |

---

## 5. Completed Engineering Phases (`DONE`)

- **Phase 1 — Multi-Tenant Cloud & OAuth 2.1 Hardening (`DONE`, PRs #1–#6):**
  - 6-table PostgreSQL 16 schema (`space`, `membership`, `place`, `object`, `lesson`, `claim`) with `FORCE ROW LEVEL SECURITY` (`loci.current_space_id`) and composite `(space_id, id)` foreign keys.
  - Vertex AI `text-embedding-004` 768-d hybrid RRF matching + 20-char homoglyph-free Mod-30 Visual Base32 tag generator (`/loci-<token>/labels.svg`).
  - OAuth 2.1 with mandatory `S256` PKCE, capability-token proof-of-possession consent, SHA-256 hashed capability tokens at rest, and stateless HMAC-signed DCR `client_id` validation.
- **Phase 1.5 — Web Console, Interactive Showcase & Tag Kit Surfaces (`DONE`, PR #7 + `linc-site#134`):**
  - Redesigned `/`, `/pricing` (`<dialog>` checkout/pilot modal), `/oauth/authorize`, `/labels-sample.svg`, and `/loci-<token>/` (`viewer.html` with live filter + 1-click Copy MCP URL).
- **Phase 2A — Token Rotation, Handoff Telemetry & Printable Tag Bundle (`DONE`, PRs #9–#11):**
  - Shipped `POST /api/spaces/{space_id}/rotate-token` and rotated all 5 Cloud SQL Spaces.
  - Instrumented `ask()` cross-actor handoff counters in `object.attrs_json` and exposed single-call gate metrics on `GET /api/spaces` and `GET /loci-<token>/api/state`.
  - Bound 12 internal bench/workshop/range assets and exported all 5 300-DPI vector SVG sheets to `.env.print-bundle/`.
