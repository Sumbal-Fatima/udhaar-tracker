"""Deterministic money logic. The AI never calculates amounts: every number it sees comes from here."""
import re
from datetime import date, datetime, timezone
from urllib.parse import quote


def _d(s: str) -> date:
    return date.fromisoformat(s)


def allocate(entries, payments):
    """Apply payments to the oldest credit first (FIFO). Returns (entries_with_remaining, unapplied_credit)."""
    ents = [
        {"id": e["id"], "amount": e["amount"], "due_on": e["due_on"], "remaining": e["amount"], "settled_on": None}
        for e in entries
    ]
    unapplied = 0.0
    for p in payments:
        amt = p["amount"]
        for e in ents:
            if amt <= 0:
                break
            if e["remaining"] <= 0:
                continue
            take = min(amt, e["remaining"])
            e["remaining"] -= take
            amt -= take
            if e["remaining"] <= 0.005:
                e["remaining"] = 0.0
                e["settled_on"] = p["paid_on"]
        unapplied += amt
    return ents, unapplied


def customer_facts(conn, customer_id: int, today: date | None = None) -> dict:
    today = today or date.today()
    c = conn.execute("SELECT * FROM customers WHERE id = ?", (customer_id,)).fetchone()
    if c is None:
        raise KeyError(customer_id)
    entries = conn.execute("SELECT * FROM ledger WHERE customer_id = ? ORDER BY due_on, id", (customer_id,)).fetchall()
    pays = conn.execute("SELECT * FROM payments WHERE customer_id = ? ORDER BY paid_on, id", (customer_id,)).fetchall()
    ents, credit = allocate(entries, pays)

    outstanding = sum(e["remaining"] for e in ents)
    overdue = [e for e in ents if e["remaining"] > 0 and _d(e["due_on"]) < today]
    overdue_amount = sum(e["remaining"] for e in overdue)
    oldest_days = max(((today - _d(e["due_on"])).days for e in overdue), default=0)

    settled = [e for e in ents if e["settled_on"]]
    late_days = [max(0, (_d(e["settled_on"]) - _d(e["due_on"])).days) for e in settled]
    avg_late = round(sum(late_days) / len(late_days), 1) if late_days else None

    last_pay = pays[-1] if pays else None

    last_sent = conn.execute(
        "SELECT sent_at FROM reminders WHERE customer_id = ? AND status = 'sent' ORDER BY sent_at DESC LIMIT 1",
        (customer_id,),
    ).fetchone()
    days_since_reminder = None
    if last_sent and last_sent["sent_at"]:
        days_since_reminder = (today - datetime.fromisoformat(last_sent["sent_at"]).date()).days

    promise = conn.execute(
        "SELECT * FROM promises WHERE customer_id = ? AND status = 'confirmed' AND promised_date >= ?"
        " ORDER BY promised_date LIMIT 1",
        (customer_id, today.isoformat()),
    ).fetchone()

    return {
        "customer_id": c["id"],
        "name": c["name"],
        "phone": c["phone"],
        "village": c["village"],
        "language": c["language"],
        "crop": c["crop"],
        "total_outstanding_pkr": round(outstanding),
        "overdue_amount_pkr": round(overdue_amount),
        "oldest_overdue_days": oldest_days,
        "last_payment_on": last_pay["paid_on"] if last_pay else None,
        "last_payment_amount_pkr": round(last_pay["amount"]) if last_pay else None,
        "settled_accounts": len(settled),
        "avg_days_late_on_settled": avg_late,
        "days_since_last_reminder": days_since_reminder,
        "active_promise": (
            {"date": promise["promised_date"], "amount_pkr": promise["amount"]} if promise else None
        ),
    }


def ai_view(facts: dict) -> dict:
    """The subset of facts sent to the model. No phone numbers or other contact details."""
    keys = (
        "customer_id", "name", "crop", "overdue_amount_pkr", "total_outstanding_pkr", "oldest_overdue_days",
        "last_payment_on", "last_payment_amount_pkr", "settled_accounts", "avg_days_late_on_settled",
        "days_since_last_reminder",
    )
    return {k: facts[k] for k in keys}


def rule_decision(facts: dict):
    """Rules that run before the AI. They protect customers from nagging and honour promises."""
    if facts["active_promise"]:
        p = facts["active_promise"]
        return {
            "action": "wait", "risk": "low", "tone": "friendly",
            "reason": f"Customer promised to pay by {p['date']}; wait until then.", "source": "rule",
        }
    if facts["days_since_last_reminder"] is not None and facts["days_since_last_reminder"] < 3:
        return {
            "action": "wait", "risk": "medium", "tone": "friendly",
            "reason": f"Reminder already sent {facts['days_since_last_reminder']} day(s) ago; avoid nagging.",
            "source": "rule",
        }
    return None


VALID_ACTIONS = {"remind_now", "wait", "escalate"}
VALID_RISK = {"low", "medium", "high"}
VALID_TONE = {"friendly", "respectful", "firm"}


def normalise_decision(raw: dict | None, facts: dict, threshold: float) -> dict:
    """Validate the model output and apply the owner's escalation threshold."""
    if not raw or raw.get("action") not in VALID_ACTIONS:
        return {
            "action": "wait", "risk": "medium", "tone": "friendly",
            "reason": "The AI did not return a usable decision for this customer; review manually.",
            "source": "rule",
        }
    d = {
        "action": raw["action"],
        "risk": raw.get("risk") if raw.get("risk") in VALID_RISK else "medium",
        "tone": raw.get("tone") if raw.get("tone") in VALID_TONE else "friendly",
        "reason": str(raw.get("reason", "")).strip()[:300] or "No reason given.",
        "source": "ai",
    }
    if facts["overdue_amount_pkr"] >= threshold and d["action"] != "escalate":
        d["action"] = "escalate"
        d["reason"] += f" (Owner rule: overdue amount is at least Rs {int(threshold):,}, so the owner decides.)"
    return d


_DIGIT_MAP = str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")


def amount_in_message(message: str, amount: float) -> bool:
    """Check that the exact overdue amount appears in the AI-written message."""
    flat = message.translate(_DIGIT_MAP).replace(",", "").replace("٬", "")
    return str(int(round(amount))) in re.sub(r"\s", "", flat) or str(int(round(amount))) in flat


def whatsapp_link(phone: str, message: str) -> str:
    digits = re.sub(r"\D", "", phone)
    if digits.startswith("0"):
        digits = "92" + digits[1:]
    return f"https://wa.me/{digits}?text={quote(message)}"


def now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
