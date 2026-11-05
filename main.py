import uuid
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from . import ai, config, db, logic, prompts, seed

STATIC_DIR = Path(__file__).resolve().parent.parent / "static"
BATCH_SIZE = 8


@asynccontextmanager
async def lifespan(app: FastAPI):
    db.init_db()
    if config.auto_seed():
        conn = db.connect()
        if conn.execute("SELECT COUNT(*) FROM customers").fetchone()[0] == 0:
            seed.seed(conn)
        conn.close()
    yield


app = FastAPI(title="Udhaar Tracker", lifespan=lifespan)


def _rows(cur):
    return [dict(r) for r in cur.fetchall()]


def _ai_call(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except ai.AIError as e:
        raise HTTPException(status_code=502, detail=str(e))


# ---------- basics ----------

@app.get("/api/health")
def health():
    return {"status": "ok", "model": config.model(), "ai_key_configured": bool(config.api_key())}


@app.get("/api/ai/models")
def models():
    return {"models": _ai_call(ai.list_models), "selected": config.model()}


@app.get("/api/ai/runs")
def ai_runs():
    conn = db.connect()
    rows = _rows(conn.execute("SELECT * FROM ai_runs ORDER BY id DESC LIMIT 50"))
    conn.close()
    return {"runs": rows, "model": config.model(), "base_url": config.base_url()}


@app.get("/api/summary")
def summary():
    conn = db.connect()
    ids = [r["id"] for r in conn.execute("SELECT id FROM customers")]
    facts = [logic.customer_facts(conn, i) for i in ids]
    out = {
        "shop_name": db.get_setting(conn, "shop_name"),
        "escalate_threshold": int(float(db.get_setting(conn, "escalate_threshold"))),
        "customers": len(facts),
        "total_outstanding": sum(f["total_outstanding_pkr"] for f in facts),
        "overdue_total": sum(f["overdue_amount_pkr"] for f in facts),
        "overdue_customers": sum(1 for f in facts if f["overdue_amount_pkr"] > 0),
        "open_escalations": conn.execute("SELECT COUNT(*) FROM escalations WHERE status='open'").fetchone()[0],
        "pending_promises": conn.execute("SELECT COUNT(*) FROM promises WHERE status='pending'").fetchone()[0],
        "draft_reminders": conn.execute("SELECT COUNT(*) FROM reminders WHERE status IN ('draft','approved')").fetchone()[0],
    }
    conn.close()
    return out


# ---------- customers, ledger, payments ----------

@app.get("/api/customers")
def customers():
    conn = db.connect()
    ids = [r["id"] for r in conn.execute("SELECT id FROM customers ORDER BY name")]
    out = [logic.customer_facts(conn, i) for i in ids]
    conn.close()
    return {"customers": out}


@app.get("/api/customers/{cid}")
def customer_detail(cid: int):
    conn = db.connect()
    try:
        facts = logic.customer_facts(conn, cid)
    except KeyError:
        raise HTTPException(404, "Customer not found")
    ledger = _rows(conn.execute("SELECT * FROM ledger WHERE customer_id=? ORDER BY due_on", (cid,)))
    payments = _rows(conn.execute("SELECT * FROM payments WHERE customer_id=? ORDER BY paid_on", (cid,)))
    promises = _rows(conn.execute("SELECT * FROM promises WHERE customer_id=? ORDER BY id DESC", (cid,)))
    conn.close()
    return {"facts": facts, "ledger": ledger, "payments": payments, "promises": promises}


class LedgerIn(BaseModel):
    customer_id: int
    amount: float = Field(gt=0)
    due_on: date
    note: str = ""


@app.post("/api/ledger")
def add_ledger(body: LedgerIn):
    conn = db.connect()
    if not conn.execute("SELECT 1 FROM customers WHERE id=?", (body.customer_id,)).fetchone():
        raise HTTPException(404, "Customer not found")
    conn.execute(
        "INSERT INTO ledger(customer_id, amount, issued_on, due_on, note) VALUES (?,?,?,?,?)",
        (body.customer_id, body.amount, date.today().isoformat(), body.due_on.isoformat(), body.note),
    )
    conn.commit()
    conn.close()
    return {"ok": True}


class PaymentIn(BaseModel):
    amount: float = Field(gt=0)
    paid_on: date | None = None


@app.post("/api/customers/{cid}/payments")
def add_payment(cid: int, body: PaymentIn):
    conn = db.connect()
    if not conn.execute("SELECT 1 FROM customers WHERE id=?", (cid,)).fetchone():
        raise HTTPException(404, "Customer not found")
    conn.execute(
        "INSERT INTO payments(customer_id, amount, paid_on, note) VALUES (?,?,?,?)",
        (cid, body.amount, (body.paid_on or date.today()).isoformat(), "Recorded in app"),
    )
    conn.commit()
    facts = logic.customer_facts(conn, cid)
    conn.close()
    return {"ok": True, "facts": facts}


# ---------- AI review (triage) ----------

@app.post("/api/review/run")
def run_review():
    conn = db.connect()
    threshold = float(db.get_setting(conn, "escalate_threshold"))
    ids = [r["id"] for r in conn.execute("SELECT id FROM customers")]
    facts = {i: logic.customer_facts(conn, i) for i in ids}
    overdue = [f for f in facts.values() if f["overdue_amount_pkr"] > 0]

    run_id = uuid.uuid4().hex[:8]
    decisions: dict[int, dict] = {}
    to_ask = []
    for f in overdue:
        rule = logic.rule_decision(f)
        if rule:
            decisions[f["customer_id"]] = rule
        else:
            to_ask.append(f)

    for i in range(0, len(to_ask), BATCH_SIZE):
        batch = to_ask[i : i + BATCH_SIZE]
        result = _ai_call(
            ai.chat_json,
            "triage",
            prompts.TRIAGE_SYSTEM,
            {"today": date.today().isoformat(), "customers": [logic.ai_view(f) for f in batch]},
        )
        by_id = {}
        for d in result.get("decisions", []) if isinstance(result, dict) else []:
            try:
                by_id[int(d.get("customer_id"))] = d
            except (TypeError, ValueError):
                continue
        for f in batch:
            decisions[f["customer_id"]] = logic.normalise_decision(by_id.get(f["customer_id"]), f, threshold)

    now = logic.now_iso()
    for cid, d in decisions.items():
        conn.execute(
            "INSERT INTO triage(run_id, customer_id, action, risk, tone, reason, source, created_at) VALUES (?,?,?,?,?,?,?,?)",
            (run_id, cid, d["action"], d["risk"], d["tone"], d["reason"], d["source"], now),
        )
        if d["action"] == "escalate":
            open_one = conn.execute(
                "SELECT 1 FROM escalations WHERE customer_id=? AND status='open'", (cid,)
            ).fetchone()
            if not open_one:
                conn.execute(
                    "INSERT INTO escalations(customer_id, reason, amount, created_at) VALUES (?,?,?,?)",
                    (cid, d["reason"], facts[cid]["overdue_amount_pkr"], now),
                )
    conn.commit()
    conn.close()
    return {"run_id": run_id, "reviewed": len(decisions), "asked_ai": len(to_ask)}


@app.get("/api/review")
def latest_review():
    conn = db.connect()
    rows = _rows(
        conn.execute(
            "SELECT t.*, c.name, c.village, c.language FROM triage t JOIN customers c ON c.id = t.customer_id"
            " WHERE t.id IN (SELECT MAX(id) FROM triage GROUP BY customer_id) ORDER BY t.id"
        )
    )
    for r in rows:
        f = logic.customer_facts(conn, r["customer_id"])
        r["overdue_amount_pkr"] = f["overdue_amount_pkr"]
        r["oldest_overdue_days"] = f["oldest_overdue_days"]
        r["total_outstanding_pkr"] = f["total_outstanding_pkr"]
    esc = _rows(
        conn.execute(
            "SELECT e.*, c.name, c.village FROM escalations e JOIN customers c ON c.id = e.customer_id"
            " ORDER BY e.status, e.id DESC"
        )
    )
    conn.close()
    return {"decisions": rows, "escalations": esc}


class DecideIn(BaseModel):
    decision: str  # approve_reminder | extend_deadline | hold


@app.post("/api/escalations/{eid}/decide")
def decide_escalation(eid: int, body: DecideIn):
    if body.decision not in ("approve_reminder", "extend_deadline", "hold"):
        raise HTTPException(400, "decision must be approve_reminder, extend_deadline or hold")
    conn = db.connect()
    row = conn.execute("SELECT * FROM escalations WHERE id=?", (eid,)).fetchone()
    if not row:
        raise HTTPException(404, "Escalation not found")
    conn.execute(
        "UPDATE escalations SET status='decided', decision=?, decided_at=? WHERE id=?",
        (body.decision, logic.now_iso(), eid),
    )
    conn.commit()
    conn.close()
    return {"ok": True, "customer_id": row["customer_id"], "decision": body.decision}


# ---------- reminders ----------

class DraftIn(BaseModel):
    customer_id: int
    language: str | None = None


@app.post("/api/reminders/draft")
def draft_reminder(body: DraftIn):
    conn = db.connect()
    try:
        facts = logic.customer_facts(conn, body.customer_id)
    except KeyError:
        raise HTTPException(404, "Customer not found")
    if facts["overdue_amount_pkr"] <= 0:
        raise HTTPException(400, "This customer has nothing overdue.")
    language = body.language or facts["language"]
    if language not in ("urdu", "roman_urdu", "english"):
        raise HTTPException(400, "language must be urdu, roman_urdu or english")
    t = conn.execute(
        "SELECT tone FROM triage WHERE customer_id=? ORDER BY id DESC LIMIT 1", (body.customer_id,)
    ).fetchone()
    tone = t["tone"] if t else "friendly"
    payload = {
        "shop_name": db.get_setting(conn, "shop_name"),
        "customer_name": facts["name"],
        "language": language,
        "tone": tone,
        "overdue_amount_pkr": facts["overdue_amount_pkr"],
        "oldest_overdue_days": facts["oldest_overdue_days"],
        "last_payment_on": facts["last_payment_on"],
    }
    message, ok = "", False
    for _ in range(2):  # one retry if the amount is missing from the message
        result = _ai_call(ai.chat_json, "reminder_draft", prompts.REMINDER_SYSTEM, payload, 600)
        message = str(result.get("message", "")).strip()
        ok = bool(message) and logic.amount_in_message(message, facts["overdue_amount_pkr"])
        if ok:
            break
    if not message:
        raise HTTPException(502, "The AI returned an empty reminder. Try again.")
    cur = conn.execute(
        "INSERT INTO reminders(customer_id, language, tone, message, amount_due, amount_check_ok, status, created_at)"
        " VALUES (?,?,?,?,?,?,?,?)",
        (body.customer_id, language, tone, message, facts["overdue_amount_pkr"], 1 if ok else 0, "draft", logic.now_iso()),
    )
    conn.commit()
    rid = cur.lastrowid
    conn.close()
    return {"id": rid, "message": message, "amount_check_ok": ok}


@app.get("/api/reminders")
def list_reminders():
    conn = db.connect()
    rows = _rows(
        conn.execute(
            "SELECT r.*, c.name, c.phone FROM reminders r JOIN customers c ON c.id = r.customer_id"
            " WHERE r.message != 'Sample earlier reminder' ORDER BY r.id DESC"
        )
    )
    for r in rows:
        r["whatsapp_link"] = logic.whatsapp_link(r["phone"], r["message"])
    conn.close()
    return {"reminders": rows}


class MessageEdit(BaseModel):
    message: str = Field(min_length=3)


@app.post("/api/reminders/{rid}/edit")
def edit_reminder(rid: int, body: MessageEdit):
    conn = db.connect()
    row = conn.execute("SELECT * FROM reminders WHERE id=?", (rid,)).fetchone()
    if not row:
        raise HTTPException(404, "Reminder not found")
    ok = logic.amount_in_message(body.message, row["amount_due"])
    conn.execute(
        "UPDATE reminders SET message=?, amount_check_ok=?, status='draft' WHERE id=?", (body.message, 1 if ok else 0, rid)
    )
    conn.commit()
    conn.close()
    return {"ok": True, "amount_check_ok": ok}


@app.post("/api/reminders/{rid}/approve")
def approve_reminder(rid: int):
    conn = db.connect()
    row = conn.execute("SELECT * FROM reminders WHERE id=?", (rid,)).fetchone()
    if not row:
        raise HTTPException(404, "Reminder not found")
    conn.execute("UPDATE reminders SET status='approved', approved_at=? WHERE id=?", (logic.now_iso(), rid))
    conn.commit()
    conn.close()
    return {"ok": True}


@app.post("/api/reminders/{rid}/sent")
def mark_sent(rid: int):
    conn = db.connect()
    row = conn.execute("SELECT * FROM reminders WHERE id=?", (rid,)).fetchone()
    if not row:
        raise HTTPException(404, "Reminder not found")
    if row["status"] == "draft":
        raise HTTPException(400, "Approve the reminder before marking it sent.")
    conn.execute("UPDATE reminders SET status='sent', sent_at=? WHERE id=?", (logic.now_iso(), rid))
    conn.commit()
    conn.close()
    return {"ok": True}


# ---------- promises ----------

class ExtractIn(BaseModel):
    customer_id: int
    text: str = Field(min_length=2)


@app.post("/api/promises/extract")
def extract_promise(body: ExtractIn):
    conn = db.connect()
    try:
        facts = logic.customer_facts(conn, body.customer_id)
    except KeyError:
        raise HTTPException(404, "Customer not found")
    today = date.today()
    result = _ai_call(
        ai.chat_json,
        "promise_extract",
        prompts.PROMISE_SYSTEM,
        {
            "today": today.isoformat(),
            "customer_name": facts["name"],
            "outstanding_pkr": facts["total_outstanding_pkr"],
            "reply_text": body.text,
        },
        600,
    )
    if not isinstance(result, dict) or not result.get("has_promise"):
        conn.close()
        return {"has_promise": False, "summary": (result or {}).get("summary", "No clear promise found.")}

    confidence = result.get("confidence") if result.get("confidence") in ("high", "medium", "low") else "low"
    promised = None
    raw_date = result.get("promised_date")
    if raw_date:
        try:
            d = date.fromisoformat(str(raw_date))
            if d < today or (d - today).days > 365:
                confidence = "low"  # a past or far-future date is not trustworthy
            else:
                promised = d.isoformat()
        except ValueError:
            confidence = "low"
    amount = result.get("amount")
    amount = float(amount) if isinstance(amount, (int, float)) and amount > 0 else None
    cur = conn.execute(
        "INSERT INTO promises(customer_id, source_text, summary, amount, promised_date, condition, confidence, status, created_at)"
        " VALUES (?,?,?,?,?,?,?,?,?)",
        (body.customer_id, body.text, str(result.get("summary", ""))[:300], amount, promised,
         result.get("condition"), confidence, "pending", logic.now_iso()),
    )
    conn.commit()
    pid = cur.lastrowid
    conn.close()
    return {
        "has_promise": True, "id": pid, "summary": result.get("summary"), "amount": amount,
        "promised_date": promised, "condition": result.get("condition"), "confidence": confidence,
    }


@app.get("/api/promises")
def list_promises():
    conn = db.connect()
    rows = _rows(
        conn.execute(
            "SELECT p.*, c.name FROM promises p JOIN customers c ON c.id = p.customer_id ORDER BY p.id DESC"
        )
    )
    conn.close()
    return {"promises": rows}


class PromiseDecision(BaseModel):
    status: str  # confirmed | rejected


@app.post("/api/promises/{pid}/decide")
def decide_promise(pid: int, body: PromiseDecision):
    if body.status not in ("confirmed", "rejected"):
        raise HTTPException(400, "status must be confirmed or rejected")
    conn = db.connect()
    row = conn.execute("SELECT * FROM promises WHERE id=?", (pid,)).fetchone()
    if not row:
        raise HTTPException(404, "Promise not found")
    if body.status == "confirmed" and not row["promised_date"]:
        raise HTTPException(400, "This promise has no concrete date, so it cannot be confirmed. Reject it or ask the customer for a date.")
    conn.execute("UPDATE promises SET status=? WHERE id=?", (body.status, pid))
    conn.commit()
    conn.close()
    return {"ok": True}


# ---------- demo ----------

@app.post("/api/demo/reset")
def reset_demo():
    conn = db.connect()
    seed.seed(conn, reset=True)
    conn.close()
    return {"ok": True}


app.mount("/", StaticFiles(directory=STATIC_DIR, html=True), name="static")
