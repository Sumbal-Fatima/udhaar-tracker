# Udhaar Tracker: plan to win Vultr Agent Rush (Challenge 2, Finance)

No plan guarantees a win. This one is built around what the judges score and what the rules require.

## 1. Rules checklist (from the hackathon page and the mentors)

- [ ] Backend VM and web app deployed on Vultr (Cloud Compute, via Coolify or Docker)
- [ ] Public demo URL that works without login
- [ ] Public GitHub repo, README explains the Vultr architecture and the use case
- [ ] Demo video (2 to 3 minutes)
- [ ] Cover image, slides, short and long description, tags
- [ ] Submitted on lablab.ai (required for judging)
- [ ] Vultr Serverless Inference used for the AI steps (optional for Challenge 2, but it earns Application of Technology points)
- [ ] Demo with sample data is allowed: the mentors confirmed on Discord that it is fine as long as judges can see the workflow and AI calls are real and the system would work live

**Deadline:** the page contradicts itself (header: Nov 9, 4:00 AM PST, about 5:00 PM PKT; schedule: Nov 9, 4:00 AM PKT). Plan to submit on the evening of **Sun 8 Nov (PKT)** and ask on Discord which one is correct.

## 2. How it scores on each criterion

| Criterion | What we show | Where |
|---|---|---|
| Application of Technology | Three distinct AI tasks (triage, multilingual drafting, promise extraction) on Vultr Serverless Inference, validated outputs, retry on bad JSON, AI log with latency and tokens, Docker on a Vultr VM | AI log tab, README |
| Business Value | Real problem: dealers lose money and goodwill on udhaar. Shows money at stake (Rs 1.69M outstanding, 20 overdue accounts), owner time saved, no-nag rules | Dashboard cards, demo video |
| Originality | Agriculture credit in Pakistan, Urdu / Roman Urdu / English, promise tracking tied to harvest reality. Other submissions are generic payables or support bots | Pitch and video |
| Presentation | One clear story, clean UI, 2 to 3 minute video, README with architecture diagram | Video, slides |

## 3. Screens

1. **Review queue (home).** Summary cards. "Run AI review" button. A table of every overdue customer: risk, action (AI or rule), one-line reason. "Needs your decision" cards for escalations.
2. **Reminders.** AI drafts in the customer's language. Amount-verified badge. Edit, approve, open in WhatsApp, mark sent.
3. **Promises.** Paste a customer reply, AI extracts amount and date, owner confirms. Confirmed promises pause reminders.
4. **Customers.** Outstanding, overdue, days overdue, last payment. Record a payment (oldest credit is paid first).
5. **AI log.** Every call to Vultr Serverless Inference: task, model, latency, tokens, errors.

## 4. Data model (SQLite)

`customers`, `ledger` (credit given, due date), `payments`, `triage` (AI and rule decisions per run), `reminders` (draft, approved, sent), `promises` (pending, confirmed, rejected), `escalations` (open, decided), `ai_runs` (audit log), `settings` (shop name, escalation threshold).

## 5. AI prompts (in `app/prompts.py`)

1. **Triage:** batches of 8 overdue customers with verified facts, returns action, risk, tone and a reason citing the facts.
2. **Reminder:** shop name, customer name, language, tone, exact amount, returns one message of at most 60 words. No threats, no invented facts.
3. **Promise extraction:** reply text plus today's date, returns has_promise, amount, date, condition, confidence.

## 6. Schedule (Pakistan time)

| When | Goal | Done when |
|---|---|---|
| **Thu 5 Nov (today)** | Get the Inference API key from the Vultr console. Run locally. Pick the model from `/api/ai/models`. Create the Vultr VM with Coolify. Push to GitHub. Deploy v0. | The public URL loads and "Run AI review" works against Vultr |
| **Fri 6 Nov** | Improve prompts with real outputs. Check the Urdu messages yourself and fix the prompt if the language is stiff. Polish the UI. Add screenshots to the README. Test the full flow 3 times on the live URL. | The whole demo runs on the live URL with no errors |
| **Sat 7 Nov** | Feature freeze by noon. Record the video, make the cover image and slides, write both descriptions. Do a dry-run of the lablab submission form. | Everything needed for submission exists |
| **Sun 8 Nov** | Buffer. Fresh-browser test of the public URL. Reset demo data. Submit by the evening. Confirm the deadline on Discord. | Submission confirmed on lablab.ai |

## 7. Demo video script (about 2:30)

1. **0:00 Problem (20s).** "A fertilizer dealer in Punjab has Rs 1.7 million out on credit. Tracking it is done in notebooks, and reminders are awkward."
2. **0:20 Review (40s).** Press Run AI review. Show 20 overdue accounts sorted into remind, wait and escalate, each with a reason. Point at the two rule-based waits (a promise and a recent reminder).
3. **0:60 Owner decision (20s).** Show a Rs 210,000 account the AI cannot act on alone. The owner chooses.
4. **1:20 Reminder (40s).** Draft in Urdu. Show the amount-verified badge. Approve, open in WhatsApp.
5. **2:00 Promise (20s).** Paste a Roman Urdu reply, show the extracted date, confirm.
6. **2:20 Vultr (10s).** Show the AI log (real calls, latency, tokens) and name the Vultr services.

Say once, clearly: "The ledger is sample data; the workflow and AI calls are live."

## 8. Risks and fixes

| Risk | Fix |
|---|---|
| The default model name is wrong or unavailable | The app lists real models at `/api/ai/models`. Set `VULTR_MODEL` to one of them. |
| Urdu output reads badly | Try another model from the list. Edit the reminder prompt. You can read Urdu, so judge it yourself. |
| Judges click "Run AI review" many times | Fine for credits ($200). Keep "Reset demo data" visible. |
| Slow AI calls during the video | Record after one warm-up run. Each review is about 3 calls. |
| Credits end | Not likely, but check the Vultr billing page on Friday and Sunday. |

## 9. What only you can do

- Create or copy the Inference API key and set it on the server.
- Create the Vultr VM and the GitHub repo (or give me access in the browser and approve each step).
- Check the Urdu text.
- Record and narrate the video.
- Press Submit on lablab.ai.
