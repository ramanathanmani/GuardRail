# GuardRail

An AI decision layer for voice customer support. Every action a voice agent wants to take
(refund, replacement, cancellation, address change) passes through four gates before it executes.

**The problem:** in Hindi, Tamil and Kannada mixed with English, speech recognition drops or flips
negation words ("refund **vendam**" → "refund **venum**"), so an agent ends up doing the opposite
of what the customer asked. GuardRail catches that before anything irreversible happens.

## How it works

A customer phones a [Vobiz](https://vobiz.ai) number and speaks code-mixed Hindi/Tamil/Kannada + English.

1. **Sarvam** transcribes the speech live.
2. **Claude** translates it to English and extracts the intent and every action mentioned.
3. **GuardRail** runs four gates: negation → SOP → risk → decision.
4. The agent replies in an **ElevenLabs** voice: it confirms, offers an alternative, hands off, or acts.
5. A **Freshdesk** ticket is created with the transcript and the decision trace.
6. Approved refunds go through **Dodo Payments** (test mode), only after the undo window closes.
7. The backend runs on **AWS EC2** (Mumbai), and every completed call is archived to **S3**.

The engine in `backend/app/guardrail/` is pure and deterministic: no network, no Claude, no DB.
Spoken lines come from templates chosen *after* the decision, never from free LLM text.

## Repository layout

```
backend/     FastAPI app (Python 3.12), SQLite via SQLModel, tests
frontend/    React + Vite dashboard (live call trace, SOP editor, savings, audit log)
deploy/      Caddyfile, systemd unit, EC2 setup script (see deploy/README.md)
ARCHITECTURE.md   Source of truth: data model, API contracts, call flow, gate rules, events
CLAUDE.md         Working rules for Claude Code on this repo
```

## Quick start

Requires Python 3.12 and Node 18+.

```bash
# backend
cd backend
python3.12 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env            # then fill in your keys
python -m app.store.seed --reset
uvicorn app.main:app --port 8000 --no-access-log
```

Use a single worker: sessions, undo timers and the event bus live in memory. Avoid `--reload`
during phone tests, since a restart wipes in-memory calls.

```bash
# frontend (separate terminal)
cd frontend
npm install && npm run dev      # http://localhost:5173
```

The Vite dev server proxies `/api`, `/v1`, `/ws`, `/vobiz` and `/health` to `localhost:8000`.

For a real phone call in development, expose the backend with ngrok and point your Vobiz number at it:

```bash
ngrok http 8000 --url=<your-reserved-domain>
```

### Running without every key

Providers are selected in `backend/.env`, and keys are only required for the provider you pick:

| Setting | Options |
|---|---|
| `HELPDESK` | `freshdesk`, `none` (local ids, offline dev) |
| `PAYMENTS` | `dodo`, `simulated` (shown as "Simulated refund") |
| `TTS_PROVIDER` | `elevenlabs`, `sarvam` |
| `AUDIT_ARCHIVE` | `s3`, `local` (writes `backend/audit/`) |
| `LLM_PROVIDER` | `anthropic`, `bedrock` |

A missing required key fails startup with the variable's name. AWS credentials never go in `.env`;
`boto3` uses the EC2 IAM role or your default AWS profile.

## Tests

```bash
cd backend && pytest -q
```

Tests mock Anthropic, Sarvam, ElevenLabs, Dodo, Vobiz, Freshdesk and S3, so they never hit real
APIs or spend credits.

## Dodo test payments

```bash
python -m app.store.dodo_setup            # create test payment links (you pay them)
python -m app.store.dodo_setup --sync     # store paid payment ids
python -m app.store.dodo_setup --status   # unrefunded payments left per order
```

## Deployment

The backend runs on EC2 (`ap-south-1`) behind Caddy (HTTPS/WSS, serves `frontend/dist`, basic auth
on the dashboard) under systemd. See [deploy/README.md](deploy/README.md) for the exact steps.
`backend/.env` and `deploy/caddy.env` are gitignored and should reach the server only by `scp`.

## Sponsors

Freshworks (Freshdesk), Anthropic (Claude), Sarvam (STT), ElevenLabs (voice), Dodo Payments
(refunds), AWS (EC2 + S3). Phone line by Vobiz.

## Status

Hackathon build. Phases 0–3 are verified. Phases 4–8 (Freshdesk, phone loop, dashboard, refunds
with undo window, S3 archive and EC2 deploy) are code complete and awaiting live checks. See
`ARCHITECTURE.md` §10 and §15 for details.
