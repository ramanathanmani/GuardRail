# GuardRail — Architecture & Build Plan (Demo-Day Edition, v3)

> An AI decision layer that sits between a voice support agent and the systems it acts on.
> Every action the agent wants to take (refund, replacement, cancellation, address change)
> passes four gates before it executes. The core problem: in Hindi, Tamil and Kannada mixed
> with English, speech recognition drops or flips negation words ("refund **vendam**" →
> "refund **venum**"), so an agent does the opposite of what the customer asked.

**Demo goal (tomorrow):** a judge dials a real Indian phone number, speaks a problem in
code-mixed Hindi/Tamil/Kannada + English, and watches live on screen:

1. Sarvam transcribes the speech as the person talks
2. Claude translates it to English and works out what the customer wants
3. GuardRail runs its four gates and picks a decision
4. The agent speaks back in an ElevenLabs voice: it asks for confirmation, offers an
   alternative, hands off, or confirms the action
5. A real ticket appears in Freshdesk with the transcript and decision trace
6. An approved refund is issued through Dodo Payments (test mode), but only after the undo
   window closes

**What changed in v3** (after a full review): the negation gate now reliably fires on the demo
line; confirmations can't confirm the wrong action; spoken replies come from templates *after*
the decision; Arjun's accepted exchange now executes; Karthik is a real seeded profile; Dodo
test payments can be refunded more than once across rehearsals; the call has a defined start,
silence handling and end; data model, seed data, events and security are specified; the build
order puts the first real phone call in the first 2–3 hours.

### Sponsor map — every sponsor does real work in the pipeline

| Sponsor | Role in GuardRail | Where |
|---|---|---|
| **Freshworks** | Where the ticket, transcript and decision trace land for human agents (Freshdesk) | `integrations/freshdesk.py` |
| **Sarvam** | Live speech-to-text for code-mixed Tamil/Hindi/Kannada + English, the input where negation errors start | `stt/sarvam_stream.py` |
| **Anthropic (Claude)** | Translates to English, extracts intent and every action mentioned, analyses negation, writes the post-call ticket summary | `agent/claude_client.py` |
| **ElevenLabs** | The agent's voice on the call (`eleven_flash_v2_5`, `ulaw_8000` goes straight into the phone line) | `tts/elevenlabs_tts.py` |
| **Dodo Payments** | The money action GuardRail guards: refunds are issued via the Dodo refunds API, held until the undo window closes | `integrations/payments.py` |
| **AWS** | Runs the backend on EC2 in Mumbai (`ap-south-1`) with a stable public address for Vobiz; archives every call's audit record to S3 (the future training set for the negation model) | `deploy/`, `store/audit_archive.py` |

Phone line: **Vobiz** (Indian telephony; the team already has API keys). Not a sponsor, but it's
the piece that gives us live call audio on an Indian number.

Pitch line: *"Sarvam hears it, Claude understands it, GuardRail decides it, ElevenLabs says it,
Dodo moves the money, Freshworks records it, all running on AWS — and nothing irreversible
happens until GuardRail says so."*

---

## 1. The phone line: Vobiz (and why not Webex)

Cisco has **no public real-time audio API** for Webex Calling or Webex Contact Center. Their
developer community says the streaming interface isn't published yet; only post-call recordings
are available. A backend can't listen to a Webex call live.

| Role | Tech | Why |
|---|---|---|
| Customer's phone | Any Indian mobile, **or the Webex app** dialing out | Judges dial a normal Indian number; Webex can still be the caller's phone on stage |
| Support line GuardRail listens on | **Vobiz Indian DID + Voice XML `<Stream>`** | Streams live call audio to our server over a WebSocket, in both directions. Sarvam publishes an official Sarvam + Vobiz voice-agent guide. |
| Fallbacks | **Typed-text mode** on the dashboard (same pipeline, no audio), plus a **recorded video** | Venue networks and phone lines fail |

Pitch line: *"GuardRail is telephony-agnostic. Today it listens on Vobiz. The same engine plugs
into Webex Contact Center once Cisco ships its real-time media API, or into any voice-AI platform
(Bolna, Ringg, SquadStack) through our `/v1/check` API."*

---

## 2. System diagram

```
 ┌────────────────────┐   PSTN    ┌──────────────────┐
 │ Customer's phone   │──────────▶│ Vobiz Indian DID │
 │ (mobile or Webex)  │◀──────────│ (support line)   │
 └────────────────────┘           └──────┬───────────┘
                                         │ POST /vobiz/answer?k=… → XML <Stream bidirectional keepCallAlive>
                                         │ WSS  /vobiz/stream/{secret} ⇄ μ-law 8 kHz audio
                                         ▼
 ┌─────────────────────────────────────────────────────────────────────────┐
 │  BACKEND on AWS EC2 (ap-south-1) · Caddy HTTPS · Python 3.12 · FastAPI  │
 │  (one process, one worker — sessions, timers and the event bus are     │
 │   in memory)                                                            │
 │                                                                         │
 │  telephony/vobiz ──audio──▶ stt/sarvam_stream (saaras realtime, codemix)│
 │     ▲                          │ transcript.final                        │
 │     │                          ▼                                         │
 │     │                  agent/conversation  (CallSession state machine)   │
 │     │                          │                                         │
 │     │                          ▼                                         │
 │     │                  agent/claude_client.understand_turn()             │
 │     │                  → English, intent, all actions mentioned, negation│
 │     │                          │                                         │
 │     │                          ▼                                         │
 │     │                  agent/intents → ProposedAction                    │
 │     │                          │                                         │
 │     │                          ▼                                         │
 │     │                  guardrail/engine.run_gates()  (pure, no I/O)      │
 │     │                  negation → SOP → risk → decision matrix           │
 │     │                          │ Decision                                │
 │     │         ┌────────────────┼──────────────────────────┐              │
 │     │         ▼                ▼                          ▼              │
 │     │  agent/templates   integrations/freshdesk     guardrail/undo       │
 │     │  (spoken reply)    ticket + notes             timer → payments →   │
 │     │         │                                     Dodo refund          │
 │     └── tts/elevenlabs (ulaw_8000 → straight to Vobiz)                   │
 │                                │                                         │
 │         events/bus ────────────┼──────────────────────┐                  │
 │         store/ (SQLite)        │                      │                  │
 │         store/audit_archive ──▶ AWS S3 (one JSON per completed call)     │
 └────────────────────────────────────────────────────────┼─────────────────┘
                                                          │ WS /ws/dashboard
                                                          ▼
                                   ┌─────────────────────────────────────┐
                                   │ DASHBOARD (React + Vite, Bolt export)│
                                   │ served by Caddy on the same host     │
                                   │ live call · decision trace · SOP     │
                                   │ rules · audit log · savings          │
                                   └─────────────────────────────────────┘
```

**Secrets live only in `backend/.env`** (and the Caddy password hash in `deploy/caddy.env`).
The browser never sees an API key.

---

## 3. Tech stack

| Layer | Choice | Notes |
|---|---|---|
| Backend | Python 3.12, FastAPI, Uvicorn **`--workers 1 --no-access-log`** | One worker, because state is in memory. No access log, because some paths carry secrets. |
| Speech-to-text | Sarvam `saaras:v3-realtime` over WebSocket (`sarvamai` SDK) | μ-law 8 kHz straight from Vobiz. Server-side VAD marks end of turn. |
| Understanding | Claude via the `anthropic` SDK, forced tool call for structured JSON | Fast model per turn; smart model for the post-call summary |
| Text-to-speech | **ElevenLabs** `eleven_flash_v2_5`, `output_format=ulaw_8000` | Vobiz `playAudio` accepts `audio/x-mulaw` 8000 Hz, so no conversion |
| Telephony | **Vobiz** Voice XML `<Stream>` (bidirectional WebSocket), Indian DID | Plain FastAPI + an XML string. `httpx` + `X-Auth-ID` / `X-Auth-Token` for REST (hangup) |
| Helpdesk | Freshdesk API v2 | Plain `httpx`, basic auth with the API key |
| Payments | **Dodo Payments** refunds API, test mode (`dodopayments` SDK) | `PAYMENTS=simulated` fallback, clearly labelled |
| Storage | SQLite via SQLModel | Schema in §4.1 |
| Live UI | WebSocket `/ws/dashboard` + REST `/api/*` | Frontend is the Bolt build, rewired to real data. **Relative URLs only.** |
| Hosting | **AWS EC2** `t3.small`, Ubuntu, `ap-south-1`, Elastic IP, **Caddy** (auto HTTPS/WSS, serves the frontend, basic auth on the dashboard), `systemd` | ngrok (reserved domain) during development |
| Audit archive | **AWS S3** (`boto3`), one JSON per completed call | IAM instance role on EC2. No AWS keys in `.env`. |

**Stretch only (build only if ahead of schedule):** browser-mic mode, Freshservice adapter,
Claude via Amazon Bedrock, Sarvam TTS fallback, barge-in.

---

## 4. Repository layout

```
guardrail/
├── CLAUDE.md
├── ARCHITECTURE.md
├── backend/
│   ├── .env / .env.example
│   ├── requirements.txt
│   ├── app/
│   │   ├── main.py                # app, CORS (dev only), routers, lifespan: DB init, seed if empty, pre-render greeting
│   │   ├── config.py              # Settings; the ONLY place env vars are read; validates keys per selected provider
│   │   ├── models.py              # Pydantic: Understanding, ProposedAction, GateContext, GateResult, Decision, DashboardEvent…
│   │   ├── telephony/
│   │   │   ├── vobiz_routes.py    # POST /vobiz/answer, POST /vobiz/hangup, POST /vobiz/stream-status, WS /vobiz/stream/{secret}
│   │   │   └── vobiz_client.py    # hangup via Vobiz REST (verify the endpoint in the Vobiz docs)
│   │   ├── stt/sarvam_stream.py   # one Sarvam realtime session per call
│   │   ├── tts/
│   │   │   ├── tts.py             # TtsClient protocol + factory; greeting-audio cache
│   │   │   ├── elevenlabs_tts.py  # ulaw_8000 stream, re-framed to 160-byte frames
│   │   │   └── sarvam_tts.py      # (stretch) fallback
│   │   ├── audio/codec.py         # 160-byte re-framing; μ-law⇄PCM16 + resampling (only if needed)
│   │   ├── agent/
│   │   │   ├── conversation.py    # CallSession state machine (§6)
│   │   │   ├── claude_client.py   # understand_turn(), summarize_call()
│   │   │   ├── intents.py         # intent → ProposedAction mapping (§7.2)
│   │   │   ├── answers.py         # yes / no / cancel word lists for CONFIRMING, OFFERING_ALT, UNDO_WINDOW
│   │   │   ├── templates.py       # every spoken line, per decision kind (§7.7)
│   │   │   └── prompts.py         # system prompt + tool schema
│   │   ├── guardrail/
│   │   │   ├── engine.py          # run_gates(ctx) → GateTrace + Decision (pure)
│   │   │   ├── negation.py        # lexicons + negation score
│   │   │   ├── sop.py
│   │   │   ├── risk.py
│   │   │   ├── fault_injection.py # DEMO ONLY, once per call, clearly labelled
│   │   │   └── undo.py            # undo-window timers (survive hang-up)
│   │   ├── integrations/
│   │   │   ├── helpdesk.py        # HelpdeskClient protocol + factory
│   │   │   ├── freshdesk.py
│   │   │   └── payments.py        # PaymentsClient: Dodo | Simulated
│   │   ├── store/
│   │   │   ├── db.py              # tables (§4.1)
│   │   │   ├── seed.py            # relative-date demo data (§4.2); --reset
│   │   │   ├── dodo_setup.py      # create payment links; --sync; --status
│   │   │   └── audit_archive.py   # completed call → S3 (or ./audit/)
│   │   ├── events/bus.py          # in-process pub/sub → dashboard sockets
│   │   └── api/
│   │       ├── public.py          # POST /v1/check
│   │       ├── sim.py             # POST /api/sim/start | turn | end (typed-text calls)
│   │       └── dashboard.py       # /api/sop, /api/calls, /api/decisions, /api/stats, /api/undo, /api/demo/*
│   └── tests/
│       ├── fixtures/understandings.json   # fixed Claude outputs for every §7.5 row
│       ├── test_engine.py
│       ├── test_negation.py
│       ├── test_conversation.py   # confirm / offer / undo flows using fixtures + typed turns
│       ├── test_payments.py
│       ├── test_vobiz.py
│       └── test_freshdesk.py
├── frontend/                      # Bolt.new export (React + Vite). No .env. Relative URLs; Vite dev proxy to :8000
└── deploy/
    ├── Caddyfile
    ├── caddy.env.example          # SITE_ADDRESS, DASHBOARD_USER, DASHBOARD_PASSWORD_HASH (real file gitignored)
    ├── guardrail.service          # systemd: uvicorn --workers 1 --no-access-log on 127.0.0.1:8000
    ├── setup_ec2.sh
    └── README.md
```

### 4.1 Data model (SQLite, SQLModel)

| Table | Columns |
|---|---|
| `customers` | `id`, `name`, `phone`, `language_code` (`ta-IN`/`hi-IN`/`kn-IN`), `account_age_days`, `avg_order_inr`, `is_seed` |
| `orders` | `id`, `customer_id`, `item`, `category`, `amount_inr`, `status` (`processing`/`delivered`), `delivered_on` (date or null), `is_seed` |
| `refund_history` | `id`, `customer_id`, `order_id`, `refunded_on` (date), `is_seed` |
| `payments` | `payment_id` (Dodo), `order_id`, `amount_inr`, `refunded` (bool), `refund_id` |
| `sop_rules` | one row: `return_window_days`, `max_refunds`, `refund_window_days`, `excluded_categories` (JSON), `high_value_hold_inr`, `require_confirm_on_negation`, `undo_window_enabled` |
| `calls` | `id`, `channel` (`phone`/`text`), `profile_id`, `caller_masked`, `started_at`, `ended_at`, `state`, `archived` (bool), `is_seed` |
| `turns` | `id`, `call_id`, `idx`, `speaker` (`customer`/`agent`), `text_asr`, `text_used`, `fault_injected`, `english`, `understanding_json`, `ts` |
| `decisions` | `id` (`dec_…`), `call_id`, `kind`, `action_type`, `amount_inr`, `reason`, `trace_json`, `first_proposed_action`, `ticket_id`, `ticket_url`, `status` (`open`/`pending_finalize`/`finalized`/`undone`/`refund_failed`), `finalize_at`, `refund_id`, `refund_status`, `refund_provider`, `latency_ms`, `created_at`, `is_seed` |

`is_seed` marks seeded history, so the code and the Audit Log can label it honestly.

### 4.2 Seed data (dates always relative to *today*, so the demo can't drift)

| Profile | Customer | Order | Refund history |
|---|---|---|---|
| **Riya** (Coimbatore) | `ta-IN`, account 340 days, avg order ₹2,000 | `ORD-88213` mixer grinder, `kitchen`, ₹2,499, delivered today−3 | none |
| **Arjun** (Delhi) | `hi-IN`, account 400 days, avg ₹600 | `ORD-77102` T-shirt, `apparel`, ₹399, delivered today−5 | 4 refunds on today−1, −3, −6, −9 |
| **Meera** (Bangalore) | `kn-IN`, account 700 days, avg ₹4,500 | `ORD-66540` sneakers, `footwear`, ₹8,500, delivered today−6 | none |
| **Karthik** (Chennai) | `hi-IN`, account 200 days, avg ₹1,500 | `ORD-55219` headphones, `electronics`, ₹1,999, status `processing` (not shipped) | none |

Default SOP rules: return window 30 days; max 3 refunds per 15 days; excluded categories
`["innerwear"]`; high-value hold above ₹5,000; confirm on negation = on; undo window = on.

Also seed about 20 historical `calls` + `decisions` over the past 14 days (`is_seed=true`), so the
Audit Log and Savings pages aren't empty. Each seeded row is labelled "sample history" in the UI.

---

## 5. External API contracts (what we call)

### 5.1 Sarvam realtime STT
- SDK: `AsyncSarvamAI(api_subscription_key=…).speech_to_text_realtime_streaming.connect(...)`.
  Params: `model="saaras:v3-realtime"`, `language_code` = the profile's language (don't rely on
  `auto` on stage), `mode="codemix"`, `endpointing="vad"`, `stream_type="fast"`, input
  **μ-law / 8000 Hz**.
- Send `{"event":"audio_input","audio":"<base64>"}`. Receive `transcript.partial`,
  `transcript.final`, `vad.*`, `error`, `session.end`.
- VAD: `silence_duration_ms` ≈ 700, `min_speech_duration_ms` 250.
- No confidence scores are documented, so negation risk comes from lexicons + Claude, not ASR
  confidence.
- Codemix output can mix scripts. The lexicons include Latin and native-script forms.
- ⚠️ **Verify in the Phase 2 spike:** the SDK namespace and kwargs, that μ-law 8 kHz is accepted,
  and that partials arrive. If μ-law is rejected: decode to PCM16 (`audioop.ulaw2lin`), resample
  to 16 kHz, send `linear16`.
- One Sarvam session per call: open on the Vobiz `start` event, close on `stop` or hang-up.

### 5.2 ElevenLabs TTS
- Streaming endpoint `POST /v1/text-to-speech/{ELEVENLABS_VOICE_ID}/stream`, header `xi-api-key`,
  body `{"text": …, "model_id": ELEVENLABS_MODEL}`, query `output_format=ulaw_8000`.
- Streamed chunks aren't frame-aligned. **Re-frame them into 160-byte (20 ms) μ-law frames** in
  `audio/codec.py`, base64 each, send as `playAudio` (§5.3), then a `checkpoint` after the last
  frame of each reply.
- **Pre-render the greeting at startup** and cache the bytes, so it plays instantly.
- Pick an Indian-English voice. Replies are English (`en-IN`) templates (§7.7).

### 5.3 Vobiz
- The Vobiz console's Voice Application has Answer URL
  `{PUBLIC_BASE_URL}/vobiz/answer?k={VOBIZ_STREAM_SECRET}` and Hangup URL
  `{PUBLIC_BASE_URL}/vobiz/hangup?k={VOBIZ_STREAM_SECRET}` (both POST). **Attach the DID to the
  application.** Callers dial with a `0` or `+91` prefix.
- `/vobiz/answer`: reject if `k` is wrong (403). Read form fields `From`, `To`, `CallUUID`.
  Register `CallUUID` as a *pending call* (expires in 60 s), bound to the current profile (§6.1).
  Return:
  ```xml
  <Response>
    <Stream bidirectional="true" keepCallAlive="true" audioTrack="inbound"
            contentType="audio/x-mulaw;rate=8000"
            statusCallbackUrl="{PUBLIC_BASE_URL}/vobiz/stream-status?k={VOBIZ_STREAM_SECRET}">
      wss://{PUBLIC_HOST}/vobiz/stream/{VOBIZ_STREAM_SECRET}
    </Stream>
  </Response>
  ```
  `PUBLIC_HOST` is derived from `PUBLIC_BASE_URL` in `config.py`. `keepCallAlive="true"` is
  mandatory, or Vobiz hangs up immediately.
- WS `/vobiz/stream/{secret}`: close immediately on a wrong secret. Incoming events: `start`,
  `media` (`media.payload` = base64 μ-law), `playedStream` (checkpoint reached), `clearedAudio`.
  On `start`, read the call id **defensively** (`start.callId`, `start.streamId`, top-level
  `callId`, `streamId`, `callUUID`, `CallUUID` — Vobiz's own docs disagree on which one carries
  it) and **log the first raw `start` event**. Match it to a pending call. If no id can be
  parsed, take the most recent pending call and log a warning.
  ⚠️ **Vobiz does not reliably send an inbound `stop` event.** Treat the WebSocket `close`
  (`WebSocketDisconnect`) as the authoritative end-of-call signal; still handle a `stop` event
  if one arrives. `audioTrack="both"` is rejected when `bidirectional="true"` — use `"inbound"`
  or omit the attribute.
- Outgoing:
  ```json
  {"event":"playAudio","streamId":"…","media":{"contentType":"audio/x-mulaw","sampleRate":8000,"payload":"<base64 160-byte frame>"}}
  {"event":"checkpoint","streamId":"…","name":"reply-3"}
  {"event":"clearAudio","streamId":"…"}
  ```
- `playedStream` for a reply's checkpoint = the agent has finished speaking (echo guard, §6.3).
- `/vobiz/stream-status` and `/vobiz/hangup`: log them. Hangup ends the session (§6.4).
- Hanging up from our side (Phase 5): **confirmed** at `docs.vobiz.ai/call/hangup-call` —
  `DELETE https://api.vobiz.ai/api/v1/Account/{VOBIZ_AUTH_ID}/Call/{CallUUID}/`, headers
  `X-Auth-ID` / `X-Auth-Token`, no body, `204` on success.
- Reference code: `vobiz-ai/Vobiz-All-XML-python`, `vobiz-ai/Vobiz-Deepgram-Voice-Agent`, and
  Sarvam's "Build a Voice Agent using Vobiz". Copy their event handling.

### 5.4 Claude
- Per customer turn: `ANTHROPIC_MODEL_FAST`, forced tool `record_understanding` (§7.1). The
  system prompt includes the customer's known order (id, item, amount, status), so Claude doesn't
  ask for things we already know.
- Timeout 8 s, one retry. If it still fails, say the "one moment" template, then HUMAN_HANDOFF.
- After the call: `ANTHROPIC_MODEL_SMART` → `summarize_call` → added to the ticket as a private
  note, **in the background**. The ticket itself never waits for this.

### 5.5 Freshdesk
- Base `https://{FRESHDESK_DOMAIN}.freshdesk.com/api/v2`, basic auth `(FRESHDESK_API_KEY, "X")`
  (the organisers' Freshworks hackathon cookbook confirms both). `FRESHDESK_DOMAIN` is the
  subdomain only; `config.py` strips `https://` and `.freshdesk.com` if a full host is pasted.
- **Requester = the bound profile's seeded name + phone**, for phone and typed calls alike. The
  real caller's number never goes to Freshdesk.
- **Create the ticket from a template (no LLM)** at the call's first terminal decision (§6.4):
  ```json
  {
    "subject": "[GuardRail] Replacement — Mixer grinder (ORD-88213)",
    "description": "<html: customer, order, decision, reason, gate trace table, transcript so far>",
    "name": "Riya", "phone": "<Riya's seeded phone>",
    "source": 3, "status": 2, "priority": 2,
    "tags": ["guardrail", "execute", "replacement", "ta-IN", "negation-flag"]
  }
  ```
  `source 3` = phone, `status 2` = open. Add `custom_fields` only when
  `FRESHDESK_CUSTOM_FIELDS=true` (the fields must exist in Freshdesk Admin first).
- Private notes: `POST /tickets/{id}/notes` `{"body": …, "private": true}` for the refund result,
  undo, and the Claude summary.
- Updates: `PUT /tickets/{id}` to change tags (`pending-finalize` → `finalized` / `undone` /
  `refund-failed`). `tags` **replaces the whole list**, so always send the full new list.
- Errors: retry only on `429`, once, after `Retry-After`. Never retry a create that timed out (it
  may have gone through); log it and continue without a ticket id.
- Dev-time only: the Freshdesk **Product MCP** (`https://{domain}.freshdesk.com/mcp`) lets Claude
  Code inspect tickets while building. The running app never uses it (see CLAUDE.md).
- Ticket URL: `https://{FRESHDESK_DOMAIN}.freshdesk.com/a/tickets/{id}`.
- One ticket per call. Later events update that ticket; they never create a second one.

### 5.6 Dodo Payments
- Test base URL `https://test.dodopayments.com`. Auth: `Authorization: Bearer
  <DODO_PAYMENTS_API_KEY>`. The SDK's environment option selects test mode; check its exact name
  in the installed package.
- `POST /refunds` `{"payment_id": "...", "reason": "GuardRail dec_…", "metadata": {"decision_id":
  "…", "call_id": "…"}}` refunds the **full** payment (omit `items`). Response: `refund_id`,
  `status` (`succeeded|failed|pending|review`), `amount`, `currency`.
- **Each payment can be refunded once, and rehearsals use them up.** So:
  1. In the Dodo dashboard (test mode), create products matching the refundable demo orders:
     sneakers ₹8,500 (Meera) and T-shirt ₹399 (Arjun, after the SOP edit).
  2. `python -m app.store.dodo_setup` creates payment links (with `metadata.order_id`) and prints
     them. **Create at least 5 for Meera and 3 for Arjun.** Pay each one with a Dodo test card.
  3. `python -m app.store.dodo_setup --sync` stores the succeeded payments in `payments`.
  4. `python -m app.store.dodo_setup --status` shows how many unrefunded payments each order has
     left. Check it before the demo.
- **`seed --reset` never touches the `payments` table.** It resets customers, orders, seed
  history, SOP defaults, and deletes non-seed calls, decisions and `refund_history` rows.
  Refunded payments stay refunded.
- At refund time, use the first unrefunded payment for that order. If there's none, emit
  `refund.failed` ("no refundable test payment left") → HUMAN_HANDOFF.
- ⚠️ Try **one** real test refund tonight. If test-mode refunds don't work, set
  `PAYMENTS=simulated` (recorded locally, labelled "Simulated refund").
- Only **refunds** touch Dodo. Replacements, cancellations and address changes are ticket-only.

### 5.7 AWS
**EC2 (Mumbai):** `t3.small` Ubuntu in `ap-south-1`, with an Elastic IP. Security group allows 22
(your IP only), 80 and 443. Uvicorn binds to `127.0.0.1:8000`.

**Caddy** (`deploy/Caddyfile`) on the site address from `deploy/caddy.env`. Order of `handle`
blocks:
1. `/vobiz/*`, `/v1/*`, `/health` → `reverse_proxy 127.0.0.1:8000`, **no basic auth** (Vobiz and
   API clients must reach them)
2. `/api/*`, `/ws/*` → `basic_auth` + `reverse_proxy`
3. everything else → `basic_auth` + serve `frontend/dist` with `try_files {path} /index.html`

The browser sends the basic-auth credentials it already has on same-origin requests, including
the WebSocket upgrade. ⚠️ Check this on EC2. If `/ws` won't connect, move `/ws/*` into block 1
(the event stream is read-only).

**During development**, the ngrok URL exposes `/api` and `/ws` without auth. Acceptable for one
night: don't share the URL, and switch the Vobiz URLs to EC2 before the demo.

**Site address:** point a subdomain you own at the Elastic IP, or use
`<ip-with-dashes>.sslip.io`. If the certificate fails, run ngrok on the EC2 box.

**Deploy:** `setup_ec2.sh` installs Python 3.12 and Caddy, clones the repo, builds the venv and
the frontend, and installs the systemd unit. You copy `backend/.env` and `deploy/caddy.env` over
with `scp`. They're never committed.

**S3:** once a call is *complete* (§6.4), write
`s3://{S3_AUDIT_BUCKET}/calls/{YYYY-MM-DD}/{call_id}.json`. It holds the profile id (never the
raw phone number), turns (`text_asr`, `text_used`, `fault_injected`, English, understanding),
gate traces, decisions, ticket, refund result and timings. Credentials come from the IAM
instance role (`s3:PutObject` on that bucket only), or the local AWS profile. Keep public access
blocked. The upload runs in the background, and a failure is logged, never raised.
`AUDIT_ARCHIVE=local` writes to `backend/audit/` instead.

---

## 6. Call flow — the `CallSession`

### 6.1 Who is calling
The dashboard has a **"Next caller"** picker: Riya / Arjun / Meera / Karthik / Auto (default
Riya). The picker **wins**. The next answered call is bound to that profile, so one phone can play
everyone. **Auto** matches the caller number to a seeded customer; if there's no match, the agent
asks for the order ID (NEED_INFO) and looks it up. If it's still unknown after 2 tries →
HUMAN_HANDOFF.

### 6.2 States
```
GREETING → LISTENING → THINKING ─┬─ NEED_INFO ─────────────▶ LISTENING
                                 ├─ CONFIRMING ─(answer)──▶ THINKING (confirmed)
                                 ├─ OFFERING_ALT ─(answer)▶ THINKING (accepted / declined)
                                 ├─ HANDOFF ──▶ CLOSING
                                 └─ EXECUTING ─┬─ refund ───▶ UNDO_WINDOW ─▶ CLOSING
                                               └─ non-cash ─▶ CLOSING
CLOSING → (goodbye played) → hang up → ENDED
```
- **GREETING:** play the cached greeting: *"Hi, you've reached QuickKart support. I'm an AI
  assistant. How can I help you today?"*
- **THINKING:** save the turn → apply fault injection if eligible (§7.6) → `understand_turn` →
  intents → `run_gates` → speak the template for the decision → publish events.
- **NEED_INFO:** speak Claude's `reply_to_customer` (the only place Claude's own words are
  spoken, besides `order_status`). After 2 NEED_INFO turns in a row → HUMAN_HANDOFF.
- **CONFIRMING** (after CONFIRM_FIRST). The question comes from a template built from the
  **pending actions**, never from Claude's free text:
  - *Choice form*: 2+ candidate actions remain after dropping the negated ones (§7.2). "Do you
    want a refund, or a replacement?"
  - *Read-back form*: exactly one candidate remains. Name the negated action if there is one:
    "Just to confirm: you want an address change, not a cancellation. Is that right?" If there's
    no negated action: "Just to confirm: you want a refund. Is that right?"

  Reading the answer (`agent/answers.py` first, then Claude only if that's inconclusive):
  - The answer **names an action** ("replacement", "aama, replacement") → that action,
    `confirmed_by_customer=True`
  - Plain **yes** (yes / haan / aama / sari / howdu / correct) → read-back form: the read-back
    action, confirmed. Choice form: re-ask the same question **once** (this doesn't count as a
    new CONFIRM_FIRST).
  - Plain **no** (no / nahi / illa / beda) → "Okay, what would you like me to do?" → LISTENING.
    The next turn is a fresh turn (Gate 1 applies).
  - **A second inconclusive answer**, or a second CONFIRM_FIRST decision in the same call →
    HUMAN_HANDOFF.
  - Confirmed answers re-gate the original turn with the chosen action and skip Gate 1 (§7.4).
- **OFFERING_ALT** (after OFFER_ALTERNATIVE): yes → action `replacement`,
  `alternative_accepted=True` → re-gate. No (or anything that isn't yes) →
  `alternative_declined=True` → re-gate → HUMAN_HANDOFF by rule 4. Both re-gates skip Gate 1.
- **EXECUTING:** create the ticket (§5.5) → speak the execute template. For a **refund**, open
  the undo window (§7.8) and keep the line open. For non-cash actions, go straight to CLOSING.
- **UNDO_WINDOW:** the agent has said "Your refund of ₹8,500 will go through in 30 seconds. Say
  'cancel' to stop it." Only a **short, explicit** cancel word undoes it (`answers.py`: cancel /
  stop / vendam / beda / ruko). Not "nahi" or "mat karo", which people say in passing. On expiry
  → Dodo → speak the result → CLOSING.
- **CLOSING:** speak the goodbye template. After its `playedStream`, hang up via Vobiz REST (if
  confirmed working).

### 6.3 Timing rules
- **Echo guard:** from the first `playAudio` of a reply until the matching `playedStream`, drop
  STT transcripts.
- **Silence:** in LISTENING / CONFIRMING / OFFERING_ALT, if there's no final transcript for
  `SILENCE_TIMEOUT_SECONDS` (8), re-prompt once ("Are you still there?"). After a second timeout
  → CLOSING (ticket tagged `no-response` if none exists).
- **Max call length** `MAX_CALL_SECONDS` (180) → CLOSING with the handoff template.
- **Typed (sim) calls** with no turn for `MAX_CALL_SECONDS` are ended automatically, so they still
  get completed and archived.

### 6.4 Ending, tickets, archive
- **Ticket:** one per call, created at the first terminal decision (EXECUTE*, HUMAN_HANDOFF), or
  at hang-up if none exists yet (tagged `call-dropped`). Every later event updates it.
- **Hang-up** (`stop`, `/vobiz/hangup`, or ours): close Sarvam, mark the call ended. **An open
  undo window keeps running** and finalizes on time.
- **Complete** = call ended **and** no decision still `pending_finalize`. Then: add the Claude
  summary note (background), archive to S3, set `archived=true`.

---

## 7. Understanding and the GuardRail engine

### 7.1 Claude's `record_understanding` (forced tool call)
Example (row R3, the *unflipped* line "Jar udanjiruku. Refund vendam, replacement anuppunga."):
```json
{
  "english_translation": "The jar is broken. I don't want a refund, send a replacement.",
  "languages_detected": ["ta", "en"],
  "intent": "refund | replacement | cancel_order | address_change | order_status | complaint | other | unclear",
  "actions_mentioned": ["refund", "replacement"],
  "entities": { "order_id": null, "product": "mixer grinder", "issue": "jar broken" },
  "negation_analysis": {
    "negation_terms": [ { "term": "vendam", "language": "ta", "meaning": "don't want", "governs": "refund" } ],
    "contradiction": false,
    "contradiction_detail": "",
    "intent_confidence": 0.8
  },
  "reply_to_customer": "Used only for NEED_INFO / order_status. Under 25 words, one question max."
}
```
For R1 (the *flipped* line "Refund venum, replacement anuppunga"), the fixture has
`negation_terms: []`, `contradiction: true` ("asks for both a refund and a replacement") and
`intent_confidence` 0.5. Nothing is negated, so both actions stay candidates → choice form.

- `actions_mentioned` lists only actions **explicitly named** in the turn, including negated ones.
  **Never inferred** (e.g. don't add "exchange" because the customer said "size galat hai").
- The system prompt says: code-mixed Indian speech; the ASR may have flipped negations; never
  resolve a conflict yourself, just report it; the known order is `<…>`.
- Yes/no answers in CONFIRMING and OFFERING_ALT are read by `answers.py` first (§6.2).

### 7.2 Intent → action (`agent/intents.py`)

| `intent` | `ProposedAction.type` | Cash amount | Gates? | What EXECUTE does |
|---|---|---|---|---|
| `refund` | `refund` | `order.amount_inr` | yes | Undo window → Dodo refund → ticket notes |
| `replacement` | `replacement` | 0 | yes | Ticket only |
| `cancel_order` | `cancellation` | 0 | yes | Ticket only |
| `address_change` | `address_change` | 0 | yes | Ticket only ("team will confirm the new address by SMS") |
| `order_status` | — | — | no | Claude's reply using the order data, then back to LISTENING |
| `complaint`, `other` | — | — | no | Ticket (HUMAN_HANDOFF template) |
| `unclear` | — | — | no | NEED_INFO re-prompt |

**Picking the pending action:** if the negation analysis says a mentioned action is negated
("don't want X"), drop X. If two or more actions remain, or all were negated, Gate 1 decides (it
will warn → choice form).

### 7.3 `GateContext`
```python
class GateContext(BaseModel):
    call_id: str
    customer: CustomerProfile            # id, name, language_code, account_age_days, avg_order_inr
    order: Order | None
    refunds_in_window: int               # refund_history rows within sop.refund_window_days
    sop: SopRules
    transcript_used: str                 # after fault injection, if applied
    understanding: Understanding
    proposed_action: ProposedAction      # type, cash_amount_inr, order_id
    actions_mentioned: list[str]
    confirmed_by_customer: bool = False
    alternative_accepted: bool = False
    alternative_declined: bool = False
```

### 7.4 The four gates (pure Python)

**Gate 1 — Negation & contradiction.** **Skipped entirely on any re-gate triggered by an answer**
(confirmation, offer accepted, offer declined). Re-gates reuse the original customer turn and its
understanding, with the answer applied. Otherwise, score (cap 1.0):

| Signal | + |
|---|---|
| A **don't-want** term (table below) within 4 tokens of an action word | 0.4 |
| **Conflicting actions:** 2+ distinct action types found in the transcript by the **action-word lexicon** (deterministic; not from Claude), e.g. refund + replacement, cancel + address change | 0.4 |
| `negation_analysis.contradiction` is true | 0.3 |
| `intent_confidence < 0.75` | 0.2 |
| A **want** term whose don't-want pair differs by one syllable (venum/vendam, beku/beda) | 0.1 |

Score ≥ **0.4** → `warn` (→ CONFIRM_FIRST, if `sop.require_confirm_on_negation`). Want-words
alone can never reach 0.4, so ordinary requests ("refund chahiye", "refund beku") pass.

Don't-want lexicon (the only +0.4 words):

| Lang | Don't-want (Latin) | Native script |
|---|---|---|
| Tamil | vendam, venda, vendaam | வேண்டாம், வேண்டா |
| Hindi | mat, nahi, nahin, "nahi chahiye" | मत, नहीं |
| Kannada | beda, bedi | ಬೇಡ |
| Telugu | vaddu | వద్దు |
| Malayalam | venda | വേണ്ട |

Want lexicon (+0.1 only): Tamil venum, venam, vendum (வேணும், வேண்டும்); Hindi chahiye, karo
(चाहिए, करो); Kannada beku (ಬೇಕು); Telugu kavali (కావాలి); Malayalam venam (വേണം). The lone
Hindi "na" is deliberately **excluded**: it's mostly a tag word.

Action-word lexicon: refund (refund, money back, paisa wapas, paise wapas, ரீஃபண்ட், रिफंड,
ರೀಫಂಡ್), replacement (replacement, replace, exchange, badal, new one, ரீப்ளேஸ்மென்ட், रिप्लेसमेंट,
एक्सचेंज), cancel (cancel, cancellation, कैंसल), address (address, pata, एड्रेस).
**After the Phase 2 phone spike, add whatever spellings Sarvam's codemix output actually produces
for these words** (look at the logged transcripts).

**Gate 2 — SOP (pass/fail checks).** Three real checks; the other rules are policy switches.

| Check | Applies to | Fails when |
|---|---|---|
| Return window | refund, replacement | `today − delivered_on > return_window_days` |
| Refund limit | **refund only** | `refunds_in_window + 1 > max_refunds` |
| Excluded category | refund, replacement | `order.category in excluded_categories` |

`cancellation` requires `order.status == processing` (shipped orders can't be cancelled → SOP
fail, no alternative). `address_change` has no SOP checks.

**Alternative allowed** (for rule 3) = the action is a refund, and a replacement would pass the
return-window and excluded-category checks.

**Gate 3 — Risk:** `high` if `refunds_in_window > max_refunds`, or (account age < 7 days and cash
amount > `high_value_hold_inr`). `medium` if `refunds_in_window ≥ 2`, or cash amount > 2 ×
`avg_order_inr`. Otherwise `low`.

**Gate 4 — Decision matrix** (ordered, first match wins):

| # | Condition | Decision |
|---|---|---|
| 1 | Gate 1 warned | `CONFIRM_FIRST` |
| 2 | `order is None` | `NEED_INFO` |
| 3 | SOP failed on a refund **and** an alternative is allowed **and not** `alternative_declined` | `OFFER_ALTERNATIVE` |
| 4 | SOP failed | `HUMAN_HANDOFF` |
| 5 | Risk `high` **and** cash amount > 0 | `HUMAN_HANDOFF` (never move cash at high risk) |
| 6 | Cash amount > `high_value_hold_inr` | `EXECUTE_WITH_HOLD` |
| 7 | Otherwise | `EXECUTE` |

- `EXECUTE_WITH_HOLD` always opens the undo window and tags the ticket `high-value-hold`.
- `EXECUTE` of a refund opens the window when `undo_window_enabled`; otherwise it refunds
  immediately.
- Non-cash EXECUTE has no window.

**GateTrace** = exactly five steps, matching the Bolt UI: 1 Transcript & understanding,
2 Negation & contradiction, 3 SOP rules, 4 Risk & abuse, 5 Decision. Each step has `status`
(`pass|warn|fail|skipped`) and a one-line `detail`.

### 7.5 Expected results (these are the tests; fixtures in `tests/fixtures/understandings.json`)

| # | Profile & turn | Gate walk | Expected |
|---|---|---|---|
| R1 | Riya, fault injection **on**: "Jar udanjiruku. Refund ~~vendam~~ **venum**, replacement anuppunga." | G1: conflicting actions refund+replacement 0.4 (+0.1 venum) → warn, **even if Claude's `contradiction` is false** | CONFIRM_FIRST, choice form |
| R2 | Riya answers "aama, replacement" | Names an action → replacement, confirmed; G1 skipped; SOP pass (3 days, kitchen); risk low; no cash | EXECUTE (replacement), ticket only |
| R3 | Riya, fault injection **off**: "…Refund vendam, replacement anuppunga." | vendam near refund 0.4 + conflict 0.4; refund is negated, so one candidate (replacement) remains | CONFIRM_FIRST, read-back "a replacement, not a refund"; "aama" → EXECUTE (replacement) |
| A1 | Arjun: "Mujhe refund chahiye, size galat hai." | G1 < 0.4 (only a want-word) pass; SOP refund limit 4+1 > 3 fail; alternative allowed | OFFER_ALTERNATIVE |
| A2 | Arjun accepts ("haan") | Replacement, `alternative_accepted`; SOP pass (limit is refund-only); risk high but no cash | EXECUTE (replacement) |
| A3 | Arjun declines ("nahi") | Refund, `alternative_declined`; SOP fail | HUMAN_HANDOFF |
| A4 | Arjun after SOP `max_refunds` = 5 | SOP 4+1 > 5 no → pass; risk 4 ≥ 2 → medium; ₹399 | EXECUTE (refund) + undo window |
| M1 | Meera: "Sole kithu hogide, refund beku." | G1 0.1 (beku) pass; SOP pass; risk low (8,500 < 2 × 4,500) | EXECUTE_WITH_HOLD (refund ₹8,500) |
| K1 | Karthik: "Order cancel mat karo, bas address change karo." | "mat" near cancel 0.4 + conflict 0.4 | CONFIRM_FIRST, read-back "address change, not a cancellation" |
| K2 | Karthik: "haan" | address_change, confirmed; no SOP checks | EXECUTE (address_change), ticket only |
| X1 | **Synthetic fixture** (not a seeded profile): account 3 days old, ₹8,500 refund, SOP passing | Risk high (new account + amount > hold); cash > 0 | HUMAN_HANDOFF (rule 5: never move cash at high risk) |

### 7.6 Fault injection (demo only, honest)
`DEMO_FAULT_INJECTION=flip_negation` (also a dashboard toggle) applies **once per call**: to the
first customer turn that contains a don't-want term. It swaps that term for its want-pair
(vendam→venum, beda→beku, "nahi chahiye"→"chahiye", and it drops "mat"). It **never** touches
CONFIRMING / OFFERING_ALT answers or undo commands.

Store both `text_asr` (the real transcript) and `text_used` (flipped). Emit `fault.injected`, so
the dashboard shows a **"Simulated ASR error"** badge with both texts. Tell the judges: *"This
reproduces the published error-rate jump on code-mixed speech. Watch GuardRail catch it."*

### 7.7 Spoken templates (`agent/templates.py`) — spoken *after* the decision

| When | Line |
|---|---|
| Greeting | "Hi, you've reached QuickKart support. I'm an AI assistant. How can I help you today?" |
| CONFIRM_FIRST, choice | "Sorry, I want to get this right. Do you want a {a} or a {b}?" |
| CONFIRM_FIRST, read-back | "Just to confirm: you want {action}, not {negated}. Is that right?" (without ", not {negated}" when nothing was negated) |
| OFFER_ALTERNATIVE | "I can't process another refund on this order, but I can send an exchange instead. Would that work?" |
| HUMAN_HANDOFF | "I'm passing this to a specialist who will call you back shortly. Your ticket number is {ticket}." |
| EXECUTE replacement | "Done. A replacement for your {item} is on its way. Your ticket number is {ticket}." |
| EXECUTE address change | "Done. I've noted the address change for your order. Our team will confirm it by SMS." |
| EXECUTE cancellation | "Done. Your order for the {item} is cancelled. Ticket {ticket}." |
| Refund, window open | "Your refund of {amount} rupees will go through in {seconds} seconds. Say 'cancel' if you want to stop it." |
| Refund issued | "Your refund has been processed." |
| Refund undone | "Okay, I've stopped that refund. Nothing has been refunded." |
| Refund failed | "I couldn't complete the refund, so a specialist will call you back." |
| Still there? | "Are you still there?" |
| Claude failed | "Sorry, one moment please." |
| Goodbye | "Thank you for calling QuickKart. Goodbye!" |

### 7.8 Undo window — money moves last
- Opens for refunds per §7.4 (`UNDO_WINDOW_SECONDS`, 30 for the demo). The decision's `status` is
  `pending_finalize` and `finalize_at` is stored. The ticket is tagged `pending-finalize`.
- **Undo** (a cancel word on the call, or `POST /api/undo/{decision_id}`) → `undone`, ticket tag
  and note, `undo.applied`, spoken undo line if the call is still live. Dodo is never called.
- **Expiry** → if the decision has no `refund_id` (idempotency), take the first unrefunded payment
  → Dodo refund → store `refund_id` → mark the payment refunded → **insert a `refund_history` row
  (`is_seed=false`)** → `refund.issued` → ticket note + tag `finalized` → speak "refund issued" if
  still live.
- **Dodo error, or no payment left** → `refund_failed`, `refund.failed`, ticket tag
  `refund-failed`, spoken failure line. **Never auto-retry.**
- Timers live in memory. On startup:
  - `pending_finalize` decisions whose `finalize_at` has **passed** → `refund_failed`
    ("interrupted by restart"). Never refunded automatically.
  - Those **not yet due** → restart their timers for the remaining time.

---

## 8. Our own API surface

### 8.1 Public, pluggable API
`POST /v1/check`, header `X-GuardRail-Key: <GUARDRAIL_API_KEY>`.
```json
// request
{
  "tenant_id": "bolna_acme",
  "call_id": "call_8231",
  "language_hint": "ta-IN",
  "transcript": { "asr_text": "Jar udanjiruku. Refund venum, replacement anuppunga." },
  "understanding": { "…optional; §7.1 shape. If omitted, GuardRail calls Claude itself…": "" },
  "proposed_action": { "type": "refund", "amount": 2499, "currency": "INR", "order_id": "ORD-88213" },
  "customer_context": { "refunds_in_window": 0, "account_age_days": 340, "avg_order_inr": 2000,
                        "order": { "category": "kitchen", "delivered_days_ago": 3, "status": "delivered" } }
}
// response
{
  "decision_id": "dec_5521",
  "decision": "CONFIRM_FIRST",
  "reason": "Conflicting actions in one turn: refund + replacement",
  "confirmation_prompt": "Sorry, I want to get this right. Do you want a refund or a replacement?",
  "trace": [ { "step": 2, "gate": "negation", "status": "warn", "detail": "…" } ],
  "latency_ms": 12
}
```
Same engine as the phone path. The tests call it with fixture understandings (no network). The
demo `curl` also passes a fixture, so it answers instantly.

### 8.2 Dashboard REST (behind basic auth on EC2)

| Endpoint | Returns / does |
|---|---|
| `GET /api/sop` / `PUT /api/sop` | The SOP row / update it (validated) |
| `GET /api/calls?limit=50` | `[{call_id, started_at, profile, channel, final_decision, ticket_url, refund_status, is_seed}]` |
| `GET /api/calls/{id}` | Full call: turns, decisions with traces, ticket, refund |
| `GET /api/decisions?kind=` | Audit Log rows |
| `GET /api/stats` | See below |
| `POST /api/undo/{decision_id}` | Undo, if still `pending_finalize` |
| `GET/POST /api/demo/profile` | Read / set "Next caller" |
| `GET/POST /api/demo/fault-injection` | Read / set `off` / `flip_negation` |
| `POST /api/sim/start {profile_id}` → `{call_id}` | Start a typed-text call (channel `text`) |
| `POST /api/sim/turn {call_id, text}` → `{reply, state, decision?}` | One customer turn, full pipeline, no audio |
| `POST /api/sim/end {call_id}` | Hang up a typed call |

`/api/stats` returns `{actions_checked, wrong_actions_prevented, money_protected_inr,
avg_decision_ms, negation_catches_by_language: {hi, ta, kn}, decisions_by_kind, per_day: [{date,
kind, count}]}`:
- `wrong_actions_prevented` = decisions where the executed action ≠ `first_proposed_action`, plus
  OFFER_ALTERNATIVE, SOP- or risk-driven HUMAN_HANDOFF, and undone refunds
- `money_protected_inr` = cash amount of refunds proposed but not executed in those cases
- Seeded rows are included, and flagged so the UI can label them

### 8.3 Dashboard live events — `WS /ws/dashboard`
Every event is `{ "type", "call_id", "ts", ...payload }`. Names are fixed; the frontend depends
on them.

| type | payload |
|---|---|
| `call.started` | `profile`, `channel: phone\|text`, `caller_masked` |
| `state` | `state` (§6.2) |
| `transcript.partial` | `text` |
| `transcript.final` | `text` |
| `fault.injected` | `original`, `flipped` |
| `understanding` | the §7.1 object |
| `gate` | `step` (1–5), `gate`, `status`, `detail` |
| `decision` | `decision_id`, `kind`, `action_type`, `amount_inr`, `reason` |
| `agent.reply` | `text` |
| `ticket.created` | `decision_id`, `ticket_id`, `url` |
| `ticket.updated` | `ticket_id`, `tags` |
| `undo.started` | `decision_id`, `seconds`, `finalize_at` |
| `undo.applied` / `undo.expired` | `decision_id` |
| `refund.issued` | `decision_id`, `refund_id`, `status`, `amount`, `currency`, `provider: dodo\|simulated` |
| `refund.failed` | `decision_id`, `error` |
| `call.ended` | `duration_s` |

The frontend replaces the Bolt build's hardcoded scenarios with these events and the REST data.
The three scenario cards become the **"Next caller"** picker (plus Karthik), and they gain a
**typed-text box** for sim mode.

---

## 9. Environment variables (`backend/.env`)

Read only in `app/config.py`. Keys are **required only for the providers selected**
(e.g. `DODO_PAYMENTS_API_KEY` only when `PAYMENTS=dodo`). A missing required key fails startup
with the variable's **name**.

| Var | Purpose |
|---|---|
| `APP_ENV` | `dev` or `demo` |
| `ANTHROPIC_API_KEY`, `ANTHROPIC_MODEL_FAST`, `ANTHROPIC_MODEL_SMART` | Claude (`claude-haiku-4-5-20251001`, `claude-sonnet-5`) |
| `SARVAM_API_KEY`, `SARVAM_STT_MODEL`, `SARVAM_STT_MODE` | STT (`saaras:v3-realtime`, `codemix`) |
| `TTS_PROVIDER` | `elevenlabs` (`sarvam` is stretch) |
| `ELEVENLABS_API_KEY`, `ELEVENLABS_VOICE_ID`, `ELEVENLABS_MODEL` | voice (`eleven_flash_v2_5`) |
| `REPLY_LANGUAGE` | `en-IN`: the language of Claude's `reply_to_customer` (the templates are English) |
| `SARVAM_TTS_MODEL`, `SARVAM_TTS_SPEAKER` | stretch fallback voice |
| `VOBIZ_AUTH_ID`, `VOBIZ_AUTH_TOKEN` | Vobiz REST (hangup) |
| `VOBIZ_PHONE_NUMBER` | shown on the dashboard |
| `VOBIZ_STREAM_SECRET` | protects the answer/hangup/status webhooks (`?k=`) and the stream path |
| `PUBLIC_BASE_URL` | `https://<EC2 host>`, or the ngrok URL in dev. `PUBLIC_HOST` is derived from it. |
| `HELPDESK` | `freshdesk` (`freshservice` is stretch) |
| `FRESHDESK_DOMAIN`, `FRESHDESK_API_KEY`, `FRESHDESK_CUSTOM_FIELDS` | helpdesk; domain is the subdomain only (`acme`) |
| `FRESHSERVICE_DOMAIN`, `FRESHSERVICE_API_KEY` | stretch |
| `PAYMENTS` | `dodo` or `simulated` |
| `DODO_PAYMENTS_API_KEY`, `DODO_ENVIRONMENT` | refunds (`test_mode`) |
| `GUARDRAIL_API_KEY` | protects `/v1/check` |
| `UNDO_WINDOW_SECONDS` | `30` |
| `SILENCE_TIMEOUT_SECONDS`, `MAX_CALL_SECONDS` | `8`, `180` |
| `DEMO_FAULT_INJECTION` | startup default: `off` or `flip_negation` |
| `LLM_PROVIDER`, `BEDROCK_MODEL_FAST`, `BEDROCK_MODEL_SMART` | `anthropic` (`bedrock` is stretch; model IDs from the Bedrock console) |
| `AWS_REGION`, `AUDIT_ARCHIVE`, `S3_AUDIT_BUCKET` | `ap-south-1`, `s3` or `local`, bucket name |
| `DATABASE_URL` | `sqlite:///./guardrail.db` |
| `FRONTEND_ORIGIN` | CORS in dev only (`http://localhost:5173`) |

`deploy/caddy.env` (on the server only): `SITE_ADDRESS`, `DASHBOARD_USER`,
`DASHBOARD_PASSWORD_HASH` (from `caddy hash-password`).

---

## 10. Build order (tonight)

Each phase ends with a **done-when** check. Don't start the next phase until it passes. The
first real phone call happens in Phase 2, so the riskiest integration is proven early.

| # | Phase | Time | Done when |
|---|---|---|---|
| 0 | Skeleton: config (provider-conditional keys), tables §4.1, relative-date seed §4.2, `/health`; write `deploy/` files (not run) | 40 min | `uvicorn` starts; seed rows exist with correct relative dates |
| 1 | Engine + intents + `/v1/check` (optional understanding) + fixtures + tests for all of §7.5 | 60 min | `pytest` green; the fixture `curl` returns CONFIRM_FIRST for R1 |
| 2 | **Phone spike (via ngrok):** `/vobiz/answer` (with `?k=`), stream WS, cached ElevenLabs greeting plays, caller audio → Sarvam → finals in the log; log the raw `start` event | 60 min | Call the number, hear the greeting, see your words in the log |
| 3 | Claude `understand_turn` + CallSession (§6) + `answers.py` + templates + `/api/sim/*` | 75 min | Typed R1→R2, A1→A2, M1, K1→K2 flows give the §7.5 results |
| 4 | Freshdesk adapter (template ticket, notes, tag updates, one ticket per call) | 45 min | A typed call creates exactly one correct ticket |
| 5 | Full phone loop: finals → session → template → ElevenLabs; echo guard; silence timeout; goodbye + hang-up | 75 min | Riya's call works end to end by voice |
| 6 | Dashboard wiring: events + REST (incl. `/api/stats`) on the Bolt UI; Next-caller picker; typed-text box; SOP page | 90 min | A call animates the decision trace live; the ticket link opens; an SOP edit changes a result |
| 7 | Undo window + Dodo (payments table, idempotency, restart safety, refund_history insert) + fault-injection toggle | 90 min | Meera's refund shows in the Dodo dashboard after the window; Undo stops it; the toggle shows the badge |
| 8 | S3 audit archive + deploy to EC2 (Caddy + auth, systemd `--workers 1`), repoint the Vobiz URLs | 75 min | Calling works with the laptop closed; the call JSON lands in S3 |
| 9 | Rehearse twice, `seed --reset`, `dodo_setup --status`, record a backup video | 45 min | Two clean runs |

**Total ≈ 10¾ hours, with little slack.** Watch the clock against the cut list below. Stretch
items (§3) only if ahead.

**Do these by hand, in parallel, before Claude Code needs them:**
- **Before Phase 2:** Vobiz Voice Application + DID attached; ngrok reserved domain; Sarvam and
  ElevenLabs keys; ElevenLabs voice ID
- **Before Phase 4:** Freshdesk trial account + API key (Profile Settings → API key). Optional:
  enable the Freshdesk MCP (Admin → Apps & Integrations → MCP) for Claude Code to inspect tickets
- **Before Phase 6:** export the Bolt project into `frontend/` (download or GitHub export)
- **Before Phase 7:** Dodo products + paid test payment links (§5.6); one manual test refund
- **Before Phase 8:** S3 bucket; EC2 + Elastic IP + DNS name + IAM role (`s3:PutObject` on the
  bucket)

**If you fall behind, cut in this order:**
1. Skip EC2 and demo from the laptop via ngrok (S3 still covers AWS).
2. On the dashboard, wire only the Live Demo and SOP pages. **Hide** Audit/Savings rather than
   show fake data.
3. If the phone loop isn't solid by ~4 AM, demo in typed-text mode plus the backup video of a
   real call.

---

## 11. Demo-day script (≈4 minutes)

1. **Setup:** dashboard on the projector (Next caller = Riya, fault injection **on**); Freshdesk
   and the Dodo test dashboard open in other tabs; `dodo_setup --status` shows payments left.
2. **Call 1 — Riya (the core problem).** Dial the Vobiz number from a mobile or Webex. Say:
   *"Jar udanjiruku. Refund vendam, replacement anuppunga."* Point to:
   - the "Simulated ASR error: vendam → venum" badge
   - trace step 2, *Negation & contradiction*, turning amber: "conflicting actions: refund +
     replacement"
   - the agent asking "Do you want a refund or a replacement?"

   Answer *"aama, replacement"* → EXECUTE (replacement) → the Freshdesk ticket appears.
3. **Call 2 — Arjun (SOP + abuse).** Fault injection off, Next caller = Arjun. *"Mujhe refund
   chahiye, size galat hai."* → 4th refund in 10 days → agent offers an exchange → *"haan"* →
   EXECUTE (replacement). Say: *"No cash left the business."*
4. **Live SOP edit.** Raise max refunds to 5 → run Arjun again in the **typed-text box** (fast) →
   EXECUTE refund with an undo window. Shows the rules are real.
5. **Call 3 — Meera (money protection).** Next caller = Meera. *"Sole kithu hogide, refund
   beku."* → EXECUTE_WITH_HOLD ₹8,500 → the agent says the refund goes through in 30 seconds →
   *"No money has moved yet."* (keep quiet near the phone during the window) → let it expire →
   the refund appears in the Dodo dashboard → its
   `refund_id` is in the ticket note. (Optional: repeat in typed mode and press **Undo** → no
   refund in Dodo.)
6. **Close:** `curl /v1/check` with the R1 fixture, then today's call records in the S3 console.
   *"Any voice platform — Bolna, Ringg, SquadStack, or Webex Contact Center once Cisco opens
   real-time media — calls this one endpoint before acting. Freshdesk shows every decision to
   human agents, Dodo only moves money after GuardRail's window closes, and every call lands in
   S3 on AWS as an audit trail and future training data."*

---

## 12. Pre-demo checklist

- [ ] Vobiz: application URLs include `?k=`; they point at the **EC2** host (or the current ngrok
      URL if you cut EC2); DID attached; balance topped up
- [ ] AWS: EC2 + Elastic IP; DNS resolves; Caddy certificate issued; basic auth works in the
      browser **and** `/ws` connects; IAM role attached; S3 bucket exists; `.env` and `caddy.env`
      copied to the server
- [ ] Freshdesk API key works (Phase 4 test ticket visible)
- [ ] Sarvam, Anthropic and ElevenLabs keys have credits; the voice sounds right on a real phone
- [ ] Dodo: `dodo_setup --status` shows ≥ 2 unrefunded payments for Meera and ≥ 1 for Arjun;
      one manual test refund succeeded (otherwise `PAYMENTS=simulated`)
- [ ] `python -m app.store.seed --reset` run **after** the last rehearsal (dates, SOP defaults,
      history)
- [ ] Test call from the venue network and the exact phone/Webex account used on stage
- [ ] Mobile hotspot; backup video of a full successful call
- [ ] `git status` shows `.env` and `caddy.env` are not tracked

---

## 13. Risks & mitigations

| Risk | Mitigation |
|---|---|
| Vobiz call connects, then hangs up | `keepCallAlive="true"` missing or wrong WS URL. Check the logged raw `start` event. |
| Calls don't reach us | DID not attached, Answer URL still the old ngrok URL, or wrong `?k=`. Check the Vobiz console logs. |
| Sarvam rejects μ-law | Convert with `ulaw2lin` + resample to 16 kHz (§5.1) |
| Choppy agent audio | Re-frame to 160-byte frames; don't send partial frames |
| Agent hears itself | Echo guard (§6.3) |
| Venue Wi-Fi | The phone path runs on AWS; only the dashboard needs the laptop's network. Hotspot. |
| EC2 certificate / basic-auth / WS trouble | Move `/ws` out of auth; ngrok on EC2; or laptop + ngrok |
| Claude slow | Fast model; spoken lines are templates; the "one moment" line after 8 s |
| Dodo payments used up | `--status` before the demo; create extra paid links; `PAYMENTS=simulated` as a last resort |
| Double refund | `refund_id` idempotency; no auto-retry; restart marks overdue windows failed, never refunds |
| Fault injection confuses a confirmation | It only applies to the first eligible turn, never to answers |
| "Is the ASR error real?" | Say it's simulated, show the badge and toggle, explain the published error-rate gap |
| Stale demo dates | Relative-date seed + `seed --reset` before the demo |

---

## 14. After the hackathon (the product path)

- Real CRM/order lookups instead of demo profiles.
- A Freshdesk Marketplace sidebar app showing the decision trace; then Freshservice and Freshchat
  adapters around the same engine.
- Multi-tenant SOP rules and API keys per voice-platform customer.
- A negation-risk classifier trained on the S3 audit archive plus synthetic code-mixed data, with
  the rule gates kept as the explainable backstop.
- A Webex Contact Center adapter once Cisco publishes real-time media.
- On AWS: ECS Fargate behind an ALB, RDS Postgres, and a shared store for sessions/timers, so it
  can run more than one worker.

---

## 15. Open decisions (Claude Code appends here)

When the spec is ambiguous and a sensible reading exists, pick the simplest one, record it here
(date, section, the question, what you chose), and keep going.

- **2026-09-25, §3/Phase 0 — Python version.** CLAUDE.md and §3 call for Python 3.12 (needed
  later for `audioop`, removed in 3.13+). This dev machine has only Python 3.14 available (no
  `python3.12` package in its apt repos, no sudo to add one). Chose: build the venv with the
  system's Python 3.14 and add `audioop-lts` to `requirements.txt` (conditional on
  `python_version >= "3.13"`) so the audio phase (§5.1/§5.2, Phase 5) still gets a working
  `audioop`. On EC2 (Phase 8, Ubuntu via `deadsnakes`), `setup_ec2.sh` still installs real
  Python 3.12, so production runs the intended interpreter; only this laptop's dev venv differs.
  If real Python 3.12 becomes available here, recreate `backend/.venv` with it and drop
  `audioop-lts` — nothing else in the code depends on the substitution.
- **2026-09-25, §9/Phase 0 — When provider-conditional keys are validated.** §9 says a missing
  required key "fails startup with the variable's name," but the build order collects keys
  per-phase (Vobiz/Sarvam/ElevenLabs before Phase 2, Freshdesk before Phase 4, Dodo before
  Phase 7) — so at Phase 0/1, most provider keys don't exist yet and a truly eager check at
  `Settings()` construction would block `uvicorn` from starting at all. Chose: `config.py`
  exposes `require_llm()` / `require_stt()` / `require_tts()` / `require_payments()` /
  `require_vobiz()` / `require_helpdesk()` / `require_audit_archive()` / `require_public_api()`,
  each raising `MissingSettingError` (naming the missing var) for the *selected* provider. Each
  provider's factory calls its own group the moment it actually constructs that provider (Phase
  2+), matching the "small interfaces with a settings-driven factory" rule. Phase 0/1 (DB, seed,
  `/health`, the engine) call none of these, so `uvicorn` starts today with a mostly-blank
  `.env`, and each later phase gets the fail-fast-with-name behavior exactly when it starts
  needing that provider.
- **2026-09-26, §7.4/Phase 1&3 — which want-terms get the +0.1 "near-homophone" bonus.** The
  signal table's wording, "a want term whose don't-want pair differs by one syllable
  (venum/vendam, beku/beda)", names exactly two pairs but could be read as "any want term with
  *some* don't-want pair" (which would also catch vendum/venam, chahiye, karo, kavali). Chose the
  literal reading — only `venum`/`வேணும்` and `beku`/`ಬೇಕು` add +0.1 — since it matches every
  named example exactly and doesn't change any §7.5 row's outcome either way (checked R1, A1, M1,
  K1 by hand: the ambiguity never flips a result across the 0.4 threshold).
- **2026-09-26, §5.4/Phase 3 — Anthropic API key not scoped to a workspace.** The first real
  `understand_turn` call failed with a 400: "This API key is not scoped to a workspace... Add the
  header, or use an API key that is scoped to a workspace." Not a code bug — some org-level
  Anthropic keys need an `anthropic-workspace-id` header. Resolved by the user generating a
  workspace-scoped key instead (Console → Settings → Workspaces → that workspace's API Keys), so
  no header/config changes were needed in `config.py`. If a future key hits this again, either
  regenerate it the same way, or add `ANTHROPIC_WORKSPACE_ID` and send it as that header.
- **2026-09-26, §7.1/Phase 3 — Claude's `actions_mentioned`/`governs` drifted from the canonical
  vocabulary.** The first live call returned verbose entries like `"refund vendam (negated -
  don't want refund)"` instead of the plain `"refund"` the §7.1 example shows, which broke
  `intents.normalize_action_word`'s exact-match lookup. Fixed by constraining both fields to a
  strict 4-word JSON Schema `enum` (`refund`/`replacement`/`cancel_order`/`address_change`) in
  `agent/prompts.py`'s tool schema, plus restating the same constraint in the system prompt.
  Separately, on the fault-injected R1 line, Claude once invented a `negation_term` on the Tamil
  verb "anuppunga" ("send it") — mistaking a request verb for a negation. Gate 1's own score was
  unaffected (its don't-want/conflicting-actions signals are lexicon-based, not Claude-based),
  but candidate filtering for the CONFIRM_FIRST template used Claude's `negation_terms` and picked
  read-back over choice form. Fixed by explicitly naming the request-verb/negation distinction
  (with examples) in the system prompt; re-ran the exact line 3× afterward with an empty
  `negation_terms` every time. Real Claude output for the required §7.5 rows now matches (verified
  live, not just the fixtures) — but this is still a probabilistic model, so watch for it recurring
  under different phrasing.
