# CLAUDE.md — GuardRail

GuardRail is an AI decision layer for voice customer support. A customer phones a Vobiz number
and speaks code-mixed Hindi/Tamil/Kannada + English. Sarvam transcribes it, Claude works out the
intent, and GuardRail runs four gates (negation → SOP → risk → decision) before any action
executes. The agent speaks back in an ElevenLabs voice and a Freshdesk ticket is created.
Approved refunds go through Dodo Payments (test mode), only after the undo window closes. The
backend runs on AWS EC2 (Mumbai), and every completed call is archived to S3.

Sponsors, all used for real: Freshworks, Anthropic, Sarvam, ElevenLabs, Dodo Payments, AWS. The
phone line is Vobiz.

**Read `ARCHITECTURE.md` before starting any task.** It's the source of truth for the data model,
API contracts, call flow, gate rules, spoken templates, events and build order. If you change a
contract, update `ARCHITECTURE.md` in the same change.

**When the spec is ambiguous:** pick the simplest reading that fits it, record it in
ARCHITECTURE.md §15 "Open decisions", and keep going. **Stop and ask only** when it involves
secrets, money (refund behaviour), deleting data, or changing an expected result in §7.5.

This is a **hackathon build with a live demo tomorrow**. Working end-to-end beats elegant. But
never break a phase that already works.

---

## Stack
- Backend: Python 3.12, FastAPI, Uvicorn (**`--workers 1`**: sessions, timers and the event bus
  are in memory), SQLModel (SQLite), httpx, pydantic-settings
- SDKs: `anthropic`, `sarvamai`, `elevenlabs`, `dodopayments`, `boto3`
- Telephony: Vobiz Voice XML `<Stream>` (plain FastAPI + an XML string; no SDK)
- Frontend: React + Vite (Bolt.new export) in `frontend/`. **No `.env`.** Relative URLs only;
  derive `wss://` from `location`. The Vite dev server proxies `/api`, `/v1`, `/ws` (with
  `ws: true`), `/vobiz` and `/health` to `localhost:8000`.
- Hosting: AWS EC2 `ap-south-1` + Caddy (HTTPS/WSS, serves `frontend/dist`, basic auth on the
  dashboard) + systemd. ngrok (reserved domain) for development.

## Commands
```bash
# backend (from backend/)
python3.12 -m venv .venv && source .venv/bin/activate
#   Windows: py -3.12 -m venv .venv ; .venv\Scripts\activate ; copy .env.example .env
pip install -r requirements.txt
cp .env.example .env              # the USER fills in keys — never write real values yourself
uvicorn app.main:app --reload --port 8000 --no-access-log
#   Drop --reload for phone tests and Phase 7+: every reload is a restart, which wipes in-memory
#   calls and turns any open undo window into refund_failed (§7.8).
pytest -q
python -m app.store.seed --reset          # relative-date demo data + SOP defaults
python -m app.store.dodo_setup            # create Dodo test payment links (user pays them)
python -m app.store.dodo_setup --sync     # store paid payment_ids
python -m app.store.dodo_setup --status   # unrefunded payments left per order

# frontend (from frontend/)
npm install && npm run dev        # http://localhost:5173

# dev tunnel for Vobiz
ngrok http 8000 --url=<reserved-domain>
```

---

## Secrets — non-negotiable
- All keys live in `backend/.env`. Read them **only** through `app/config.py` (`Settings`).
  Never call `os.environ` / `os.getenv` anywhere else.
- Never hardcode, print, log or echo a key, not even partially, and never put one in a test,
  fixture or error message. Log `"<set>"` / `"<missing>"` instead.
- Keys are required **only for the selected provider** (e.g. the Dodo key only when
  `PAYMENTS=dodo`). A missing required key fails startup with the variable's **name**.
- **AWS credentials never go in `.env`.** `boto3` finds them: the IAM role on EC2, the default
  profile locally.
- `VOBIZ_STREAM_SECRET` guards `/vobiz/answer`, `/vobiz/hangup` and `/vobiz/stream-status` (query
  `?k=`) and the stream path. Reject mismatches, and never log full Vobiz URLs. Compare this and
  `X-GuardRail-Key` with `secrets.compare_digest`, not `==`.
- If the user adds the Freshdesk Product MCP to Claude Code (see below), its API key must stay in
  **local** MCP scope (the default for `claude mcp add`), never a project `.mcp.json`. `.mcp.json`
  is gitignored as a backstop.
- `deploy/caddy.env` (dashboard password hash) and `backend/.env` are gitignored and reach the
  server only by `scp`. Before any commit, run `git status` and confirm neither is tracked.
- Keep `backend/.env.example` and `deploy/caddy.env.example` in sync with every variable, with
  empty values.

---

## Build order
Follow `ARCHITECTURE.md §10` in order. At the end of each phase:
1. Run its "done when" check and `pytest -q`.
2. Tell the user exactly how to verify it (a curl command, a URL, "call the number", or a typed
   sim conversation).
3. List the manual steps the user must do before the next phase (§10 "Do these by hand").
4. Don't start the next phase until the current one passes.

**If a "done when" needs the user** (a real phone call, a key, a paid Dodo link) and they
haven't confirmed it yet: finish and test everything you can, mark the phase "code complete —
awaiting user check" below, and move on to the next phase that doesn't depend on it (Phase 3
and 4 don't need the phone; Phase 5 does). Never mark it passed on their behalf.

**Frontend source:** the Bolt UI must be exported into `frontend/` (Bolt → download / GitHub
export) before Phase 6. If `frontend/` is empty when you get there, ask the user for it. Don't
scaffold a new UI.

Current phase: **6 code complete — awaiting user check** (dashboard built in `frontend/`, see §15) (update this line as phases
complete). Phases 0, 2 and 3 passed their done-when checks (see ARCHITECTURE.md §15). Phase 1 is
complete: `/v1/check` is now built (`app/api/public.py`).
**Code complete, awaiting user check** (51 tests green; typed smoke test against real Claude OK):
- Phase 4 (Freshdesk): needs `FRESHDESK_DOMAIN` + `FRESHDESK_API_KEY` in `.env`, then one typed call
  → exactly one ticket. Verified so far only with `HELPDESK=none` + mocked Freshdesk tests.
- Phase 5 (full phone loop, echo guard, silence, goodbye + hang-up): needs a real call as Riya.
- Phase 7 (undo window, Dodo refunds, restart safety, fault toggle): needs `DODO_PAYMENTS_API_KEY`,
  Dodo products and paid links (`dodo_setup`), then Meera's refund showing in the Dodo dashboard.
  Verified with `PAYMENTS=simulated` + mocked Dodo tests.
- Phase 8 (S3 archive + EC2): archive code done and verified with `AUDIT_ARCHIVE=local`; needs
  `S3_AUDIT_BUCKET` + the EC2 deploy (the user runs it).
Phase 4 passed: a typed call created exactly one real Freshdesk ticket (#4, then #5/#6 via the dashboard).
Phase 6: dashboard verified in the browser (Riya R1→R2 live trace + ticket link, Meera hold + Undo,
Savings/Audit render). Needs the user's own look on the SOP edit → Arjun A4 flow.

---

## Rules that are easy to get wrong
**Engine**
- `app/guardrail/` is **pure and deterministic**: no network, no Claude, no DB. `GateContext` in,
  `GateTrace` (exactly 5 steps) + `Decision` out.
- Gate 1 scoring and lexicons are exactly as in §7.4. Among lexicon words, **only don't-want
  terms score +0.4** (want-words +0.1). Separately, conflicting actions in one turn score +0.4
  (this is what catches the demo's flipped negation). The lone Hindi "na" is excluded.
- The decision matrix (§7.4) is an **ordered list of rules; the first match wins**. Never move
  cash at high risk (rule 5). The refund-limit SOP check applies to **refunds only**.
- The conflicting-actions signal comes from the **action-word lexicon only** (deterministic).
  Claude's `actions_mentioned` lists only explicitly named actions and is used to pick
  candidates, not to score.
- **Any re-gate triggered by an answer** (confirmation, offer accepted or declined) reuses the
  original turn and skips Gate 1.
- Every row of §7.5 is a test, driven by `tests/fixtures/understandings.json` (X1 is a synthetic
  fixture). Don't change the expected results to make tests pass. If a row seems wrong, stop and
  ask.

**Conversation**
- **Spoken lines come from `agent/templates.py`, chosen *after* the decision.** Claude's
  `reply_to_customer` is spoken only for NEED_INFO and `order_status`. A `complaint` with no
  action named is asked once "replacement or refund?" (template), then HUMAN_HANDOFF; `other` gets
  HUMAN_HANDOFF and a ticket (§7.2, §15).
- CONFIRM_FIRST questions are built from the pending candidate actions (choice form vs read-back
  form, §6.2), never from Claude's free text. Answers go through `agent/answers.py` first. An
  answer that names an action confirms *that* action. A confirmed turn skips Gate 1. A second
  unresolved CONFIRM_FIRST → HUMAN_HANDOFF.
- Fault injection: **once per call**, only on the first turn containing a don't-want term, never
  on confirmation or offer answers or undo commands. Always emit `fault.injected` and store both
  `text_asr` and `text_used`.
- **One Freshdesk ticket per call**, created from a template (no LLM) at the first terminal
  decision. Later events update it. The Claude summary is a background private note after the
  call.

**Freshdesk**
- The requester is the **bound profile's seeded name + phone**, for phone and typed calls alike.
  Never put the real caller's number (a judge's phone) into Freshdesk.
- `PUT /tickets/{id}` with `tags` **replaces the whole list**. To swap `pending-finalize` for
  `finalized`, send the full new list (keep `guardrail`, the language, `negation-flag`, …).
- Retry only on `429`, once, after `Retry-After`. **Never retry a ticket create that timed out**
  (it may have succeeded, and you'd get a second ticket). Log it and carry on without a ticket id.
- `FRESHDESK_DOMAIN` is the subdomain only (`acme`, not `acme.freshdesk.com`). `config.py`
  strips `https://` and `.freshdesk.com` if the user pastes the full host.
- Silence and max-length timeouts, greeting, goodbye and hang-up are as in §6.2–6.4.

**Money (Dodo)**
- Only **refunds** call Dodo, for the full amount of the first unrefunded payment for that order
  (`payments` table).
- A refund happens **only at undo-window expiry**. Undo means Dodo is never called.
- Idempotency: never call Dodo if the decision already has a `refund_id`. **Never auto-retry.**
  On startup, overdue `pending_finalize` decisions become `refund_failed` and are never refunded
  automatically. Windows that aren't yet due get their timers restarted.
- A finalized refund inserts a `refund_history` row (`is_seed=false`).
- **`seed --reset` never touches the `payments` table.**
- Voice undo accepts only short, explicit words (cancel / stop / vendam / beda / ruko).
- `PAYMENTS=simulated` must show as "Simulated refund" (`provider: simulated`).

**Telephony + audio**
- `/vobiz/answer` returns `<Stream bidirectional="true" keepCallAlive="true" audioTrack="inbound"
  contentType="audio/x-mulaw;rate=8000">`. **Without `keepCallAlive`, Vobiz hangs up.**
- Parse the `start` event defensively (the call id's field name varies) and log the first raw
  one (without secrets).
- Caller μ-law 8 kHz goes straight to Sarvam. If Sarvam rejects it, convert (§5.1).
- ElevenLabs `ulaw_8000` stream → **re-frame into 160-byte frames** → `playAudio`
  (`audio/x-mulaw`, 8000) → `checkpoint` after each reply. The greeting audio is pre-rendered
  at startup.
- Echo guard: drop STT transcripts from the first `playAudio` of a reply until its
  `playedStream`.
- `audioop` is gone in Python 3.13. Use 3.12, or `audioop-lts`.

**General**
- Async everywhere in request/WS paths. Wrap sync calls (`boto3`, sync SDK methods) in
  `asyncio.to_thread`.
- Providers sit behind small interfaces with a settings-driven factory: helpdesk, payments, TTS,
  LLM, audit archive. Nothing imports a concrete provider directly.
- Every `CallSession` state change publishes a `DashboardEvent` using the **exact** names and
  payloads in §8.3.
- Logging uses a `call_id` on every line. Mask phone numbers. Never log audio or keys.
- S3 archiving never breaks a call: it runs in the background, and failures are logged.

## External APIs — check, don't guess
Before writing code against an external API, check the docs or reference code:
- Vobiz: vobiz.ai/docs, GitHub `vobiz-ai/Vobiz-All-XML-python` and
  `vobiz-ai/Vobiz-Deepgram-Voice-Agent`, Sarvam's "Build a Voice Agent using Vobiz". **The
  hangup REST endpoint must be confirmed in the docs.** If it can't be, skip hanging up; don't
  guess it.
- Sarvam: docs.sarvam.ai (realtime STT SDK namespace and kwargs)
- ElevenLabs: elevenlabs.io/docs
- Dodo: docs.dodopayments.com (the SDK's environment option)
- Freshdesk: developers.freshdesk.com/api (v2), and the official sample code at
  `github.com/freshdesk/fresh-samples` (Python folder — auth header, ticket-create payload shape,
  note-add) when the docs' prose is ambiguous about an exact request/response shape. Base URL
  `https://{domain}.freshdesk.com/api/v2/{resource}`; auth is HTTP Basic with the API key as
  username and the literal string `X` as password (`httpx.BasicAuth(api_key, "X")` — not a real
  password, don't treat it as a secret to fill in).
- Freshservice (stretch only): docs at api.freshservice.com/v2; calls go to
  `https://{domain}.freshservice.com/api/v2/{resource}`, same Basic auth (key + `X`).
- Claude: docs.claude.com

**Model IDs are correct as written — don't "fix" them from memory.** `claude-sonnet-5`,
`claude-haiku-4-5-20251001`, `saaras:v3-realtime` and `eleven_flash_v2_5` may be newer than your
training data. If an API actually rejects one, tell the user; don't swap in an older name.

**Freshworks hackathon cookbook** (the organisers' "Freshdesk + Freshservice Hackathon Cookbook",
18 Sep 2026; the user has the PDF). It confirms the REST details above. Two more things in it:
- **Freshdesk Product MCP** (GA 10 Sep 2026): a hosted MCP server on the user's tenant,
  `https://{domain}.freshdesk.com/mcp`, auth = API key, exposing the REST API as tools
  (`fetchTicket`, `fetchTickets`, `createTicket`, `createTicketNote`, …). The cookbook lists it
  for Growth/Pro/Enterprise plans with per-minute and monthly action caps.
  - **Never use it inside the running app.** GuardRail calls REST directly (thin wrapper, mocked
    in tests).
  - **Useful to you as a dev-time tool from Phase 4 on**: checking that a ticket, its tags and
    notes really look right in Freshdesk, without throwaway curl scripts. It's optional. If you
    want it, ask the user to enable it (Freshdesk Admin → Apps & Integrations → MCP) and run:
    `claude mcp add --transport http freshdesk https://<domain>.freshdesk.com/mcp --header
    "Authorization: <api key>"` (local scope; they type the key, not you).
  - **Read-only by default.** Don't create or update tickets through it without asking: that
    clutters the ticket list shown on stage and spends the action cap.
- **Automation Rules / Workflow Automator** (no-code: on ticket create/update → set fields, add
  tags, call a webhook). GuardRail's engine already owns these decisions, so don't use them
  unless the user asks.

Or read the installed package source. Put a thin wrapper around each API so tests can mock it.
Tests must never hit real APIs or spend credits.

Claude calls use a forced tool call (`record_understanding`), model names from settings, an 8 s
timeout, one retry, then the "one moment" line and HUMAN_HANDOFF.

## Deployment (Phase 8)
Write the `deploy/` files in Phase 0 (Caddyfile with the handle order in §5.7, systemd unit with
`--workers 1 --no-access-log` on `127.0.0.1:8000`, `setup_ec2.sh`, README). **Don't run anything
against AWS or change the Vobiz console yourself.** Give the user exact commands and console
steps.

## Demo honesty
- Simulated ASR errors are always visibly labelled.
- Seeded history rows have `is_seed=true` and are labelled "sample history" in the UI.
- If a page can't be wired to real data in time, hide it. Don't fake it.

## Testing
- `test_engine.py`: every §7.5 row, plus "high risk never moves cash".
- `test_negation.py`: lexicons (Latin and native script), conflicting-action detection, "na"
  excluded.
- `test_conversation.py` (typed turns + fixtures): choice vs read-back confirmation, correction
  by naming an action, the second-CONFIRM_FIRST handoff, offer accept/decline, undo by voice,
  fault injection applied once and never to answers, one ticket per call.
- `test_payments.py`: refund only at expiry, undo means no Dodo call, idempotency, restart safety
  (overdue → failed, not yet due → restarted), "no payment left" → `refund.failed`,
  `seed --reset` leaves `payments` intact.
- `test_vobiz.py`: wrong `k` → 403; answer XML has `keepCallAlive` and the secret path; wrong
  stream secret is rejected; `start` parses with every call-id variant.
- `test_freshdesk.py`: Basic auth is `(key, "X")`; requester is the profile, not the caller;
  a tag update sends the full list; `custom_fields` only when the flag is on; a create timeout
  isn't retried; `FRESHDESK_DOMAIN` normalisation.
- Mock Anthropic, Sarvam, ElevenLabs, Dodo, Vobiz, Freshdesk and S3.
- Don't claim a phone call works until the user confirms a real call.

## Don'ts
- No new services, queues or databases (no Redis, Celery or Docker unless asked).
- Don't redesign the frontend's look (now a Freshdesk-style light theme built on Crayons values, §15). Rewire its data to `/ws/dashboard` + `/api/*`, turn
  the scenario cards into the Next-caller picker (plus Karthik), and add the typed-text box.
- Build stretch items (browser mic, Freshservice, Bedrock, Sarvam TTS, barge-in) only when the
  user says to.
- Don't commit, push or deploy unless the user asks.
