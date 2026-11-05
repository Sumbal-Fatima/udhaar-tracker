# Udhaar Tracker

An AI agent that helps agri-input dealers in Pakistan collect money they are owed.

Dealers sell seed, fertilizer and pesticide to farmers on credit (*udhaar*) and collect after harvest. Money gets forgotten, reminders are awkward to write, and nobody tracks who promised what. Udhaar Tracker reads the ledger, decides who needs a reminder today, writes the message in the farmer's language, logs promises, and sends the big decisions to the shop owner.

Built for the **Vultr: Agent Rush Hackathon** (Challenge 2: Powering the Future of Work, Finance workflow: managing payment follow-ups).

## What the agent does

1. **Reviews the ledger.** The system computes verified facts for every customer (overdue amount, days overdue, payment history, last reminder). The AI sees these facts, never raw guesses.
2. **Decides per customer.** For each overdue account the AI chooses `remind_now`, `wait` or `escalate`, rates the risk, picks a tone, and explains why in one sentence.
3. **Drafts reminders** in Urdu script, Roman Urdu or English. The app checks that the exact overdue amount appears in the message and flags it if not.
4. **Logs promises.** The dealer pastes a customer's reply; the AI extracts the amount and date, and the dealer confirms it. Confirmed promises pause reminders until the promised date.
5. **Escalates to the owner.** Large or risky accounts are never contacted automatically. The owner chooses: approve a polite reminder, extend the deadline, or hold.

## Guardrails (why it is safe to trust)

| Risk | Control |
|---|---|
| AI gets a number wrong | Amounts come from the database. The AI never calculates. Drafts are checked for the exact amount. |
| Nagging customers | Rule: no reminder within 3 days of the last one; confirmed promises are honoured until their date. |
| Big accounts handled badly | Owner rule: overdue at or above the threshold (default Rs 150,000) always goes to the owner, even if the AI says "remind". |
| Wrong or invented dates | Promise dates must be in the future and within a year, otherwise confidence drops to low and the promise cannot be confirmed. |
| Bad AI output | JSON is validated. Missing or invalid decisions become "review manually", never a silent default. One automatic retry on invalid JSON. |
| Message sent without consent | Nothing is sent automatically. The dealer approves each message, then opens it in WhatsApp with the text pre-filled. |
| Privacy | Phone numbers are never sent to the model. |
| Threats or shaming | Prompt forbids it, including for the firm tone. |

## Architecture

```
 Browser (dealer)                 Vultr Cloud Compute VM
 ┌───────────────┐   HTTPS   ┌──────────────────────────────────────┐
 │ Web app       │──────────▶│ FastAPI backend (Docker)             │
 │ review queue  │           │  - ledger + FIFO payment logic       │
 │ reminders     │           │  - rules and guardrails              │        ┌──────────────────────────┐
 │ promises      │           │  - SQLite (mounted volume)           │──HTTPS▶│ Vultr Serverless         │
 │ AI log        │           │  - ai.py: chat completions client    │        │ Inference                │
 └───────────────┘           └──────────────────────────────────────┘◀───────│ api.vultrinference.com/v1│
                                                                              └──────────────────────────┘
```

## How it uses Vultr

- **Vultr Cloud Compute:** the backend and the web app run in one Docker container on a Vultr VM (deployed with Coolify).
- **Vultr Serverless Inference:** every AI step (triage, reminder drafting, promise extraction) calls the OpenAI-compatible chat completions endpoint. The **AI log** tab shows each call, its model, latency and token counts, so the calls are visibly real.

## Demo data

The ledger is sample data for a fictional shop (all names and phone numbers are made up) so the workflow can be demonstrated safely. The workflow, the rules and the AI calls are real: connect a real ledger and the same system runs on it.

## Run locally

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env          # then fill in VULTR_INFERENCE_API_KEY
export $(grep -v '^#' .env | xargs)
uvicorn app.main:app --reload --port 8000
# open http://localhost:8000
```

Pick a model: open `http://localhost:8000/api/ai/models` and put one of the listed ids in `VULTR_MODEL`.

## Deploy on a Vultr VM

1. Create a Cloud Compute instance (the Coolify marketplace app is the easiest route).
2. In Coolify, add this GitHub repo as an application. It detects the `Dockerfile`.
3. Set the environment variables from `.env.example`. Add a persistent storage mount at `/app/data`.
4. Expose port `8000`, deploy, and open the public URL.

Or plain Docker on any Vultr VM:

```bash
docker build -t udhaar-tracker .
docker run -d --restart unless-stopped -p 80:8000 -v udhaar-data:/app/data \
  -e VULTR_INFERENCE_API_KEY=... -e VULTR_MODEL=... udhaar-tracker
```

## Tests

```bash
pip install pytest
python -m pytest -q
```

The tests replace only the network call to the model. Parsing, rules, validation, the amount check and AI-call logging all run for real.

## Limitations

- Reminders are sent by the dealer through a WhatsApp link, not by a WhatsApp Business API integration.
- One shop per deployment; no user login yet.
- Urdu quality depends on the chosen model; the dealer reviews and can edit every draft before sending.
