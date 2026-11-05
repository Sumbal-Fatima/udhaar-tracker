"""Tests stub only the network call (app.ai._post). Parsing, rules, validation and logging run for real."""
import json
from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from app import ai, logic


@pytest.fixture()
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_PATH", str(tmp_path / "t.db"))
    monkeypatch.setenv("VULTR_INFERENCE_API_KEY", "test-key")
    monkeypatch.setenv("AUTO_SEED", "true")
    from app.main import app

    with TestClient(app) as c:
        yield c


def fake_model(monkeypatch, handler):
    def _post(payload):
        system = payload["messages"][0]["content"]
        user = json.loads(payload["messages"][-1]["content"]) if payload["messages"][-1]["content"].startswith("{") else {}
        content = handler(system, user)
        return {"choices": [{"message": {"content": content}}], "usage": {"prompt_tokens": 10, "completion_tokens": 5}}

    monkeypatch.setattr(ai, "_post", _post)


def triage_handler(system, user):
    if "collections assistant" in system:
        return json.dumps({"decisions": [
            {"customer_id": c["customer_id"], "action": "remind_now", "risk": "low", "tone": "friendly", "reason": "Test reason."}
            for c in user["customers"]
        ]})
    if "payment reminders" in system:
        return json.dumps({"message": f"Salam {user['customer_name']}, Rs {user['overdue_amount_pkr']:,} baqaya hai. - {user['shop_name']}"})
    raise AssertionError("unexpected prompt")


# ---------- pure logic ----------

def test_fifo_allocation_settles_oldest_first():
    entries = [
        {"id": 1, "amount": 100, "due_on": "2026-01-01"},
        {"id": 2, "amount": 200, "due_on": "2026-02-01"},
    ]
    ents, credit = logic.allocate(entries, [{"amount": 150, "paid_on": "2026-03-01"}])
    assert ents[0]["remaining"] == 0 and ents[0]["settled_on"] == "2026-03-01"
    assert ents[1]["remaining"] == 150
    assert credit == 0


def test_amount_check_handles_commas_and_urdu_digits():
    assert logic.amount_in_message("Rs 45,000 baqaya", 45000)
    assert logic.amount_in_message("۴۵۰۰۰ روپے", 45000)
    assert not logic.amount_in_message("Rs 4,500 baqaya", 45000)


def test_whatsapp_link_normalises_pakistani_numbers():
    assert logic.whatsapp_link("0300 1234567", "hi there").startswith("https://wa.me/923001234567?text=hi%20there")


# ---------- API ----------

def test_seed_and_summary(client):
    s = client.get("/api/summary").json()
    assert s["customers"] == 24
    assert s["overdue_total"] > 0


def test_review_creates_decisions_and_logs_ai_calls(client, monkeypatch):
    fake_model(monkeypatch, triage_handler)
    r = client.post("/api/review/run")
    assert r.status_code == 200 and r.json()["asked_ai"] > 0
    decisions = client.get("/api/review").json()["decisions"]
    actions = {d["customer_id"]: d for d in decisions}
    # customer 1 has a confirmed promise and customer 7 was reminded yesterday: rules, not AI
    assert actions[1]["source"] == "rule" and actions[1]["action"] == "wait"
    assert actions[7]["source"] == "rule" and actions[7]["action"] == "wait"
    runs = client.get("/api/ai/runs").json()["runs"]
    assert runs and all(x["kind"] == "triage" and x["ok"] == 1 for x in runs)


def test_owner_threshold_forces_escalation_even_if_ai_says_remind(client, monkeypatch):
    fake_model(monkeypatch, triage_handler)
    client.post("/api/review/run")
    esc = client.get("/api/review").json()["escalations"]
    assert esc, "large overdue accounts must reach the owner"
    big = [d for d in client.get("/api/review").json()["decisions"] if d["overdue_amount_pkr"] >= 150000]
    assert big and all(d["action"] == "escalate" for d in big)


def test_bad_model_output_falls_back_to_manual_review(client, monkeypatch):
    # The model returns decisions for nobody; every asked customer must become a safe "wait".
    fake_model(monkeypatch, lambda s, u: json.dumps({"decisions": []}))
    client.post("/api/review/run")
    ai_rows = [d for d in client.get("/api/review").json()["decisions"] if d["source"] == "rule" and "did not return" in d["reason"]]
    assert ai_rows


def test_invalid_json_is_retried_then_reported(client, monkeypatch):
    fake_model(monkeypatch, lambda s, u: "I cannot do that")
    r = client.post("/api/review/run")
    assert r.status_code == 502
    runs = client.get("/api/ai/runs").json()["runs"]
    assert len(runs) == 2 and all(x["ok"] == 0 for x in runs)


def test_missing_api_key_gives_clear_error(client, monkeypatch):
    monkeypatch.setenv("VULTR_INFERENCE_API_KEY", "")
    r = client.post("/api/review/run")
    assert r.status_code == 502 and "VULTR_INFERENCE_API_KEY" in r.json()["detail"]


def test_reminder_draft_checks_amount(client, monkeypatch):
    fake_model(monkeypatch, triage_handler)
    overdue = next(c for c in client.get("/api/customers").json()["customers"] if c["overdue_amount_pkr"] > 0)
    r = client.post("/api/reminders/draft", json={"customer_id": overdue["customer_id"]}).json()
    assert r["amount_check_ok"] is True
    # a message that drops the amount is flagged, not hidden
    fake_model(monkeypatch, lambda s, u: json.dumps({"message": "Please pay soon"}))
    r2 = client.post("/api/reminders/draft", json={"customer_id": overdue["customer_id"]}).json()
    assert r2["amount_check_ok"] is False


def test_cannot_mark_sent_before_approval(client, monkeypatch):
    fake_model(monkeypatch, triage_handler)
    cid = next(c for c in client.get("/api/customers").json()["customers"] if c["overdue_amount_pkr"] > 0)["customer_id"]
    rid = client.post("/api/reminders/draft", json={"customer_id": cid}).json()["id"]
    assert client.post(f"/api/reminders/{rid}/sent").status_code == 400
    assert client.post(f"/api/reminders/{rid}/approve").status_code == 200
    assert client.post(f"/api/reminders/{rid}/sent").status_code == 200


def test_promise_extraction_and_validation(client, monkeypatch):
    future = (date.today() + timedelta(days=12)).isoformat()
    fake_model(monkeypatch, lambda s, u: json.dumps({
        "has_promise": True, "amount": 20000, "promised_date": future, "condition": None,
        "confidence": "high", "summary": "Will pay 20,000 in 12 days."}))
    r = client.post("/api/promises/extract", json={"customer_id": 2, "text": "12 din mein 20000 de dunga"}).json()
    assert r["has_promise"] and r["promised_date"] == future and r["confidence"] == "high"
    assert client.post(f"/api/promises/{r['id']}/decide", json={"status": "confirmed"}).status_code == 200

    # a promise dated in the past is downgraded and has no usable date, so it cannot be confirmed
    past = (date.today() - timedelta(days=3)).isoformat()
    fake_model(monkeypatch, lambda s, u: json.dumps({
        "has_promise": True, "amount": None, "promised_date": past, "condition": None,
        "confidence": "high", "summary": "x"}))
    r2 = client.post("/api/promises/extract", json={"customer_id": 3, "text": "kal de dunga"}).json()
    assert r2["confidence"] == "low" and r2["promised_date"] is None
    assert client.post(f"/api/promises/{r2['id']}/decide", json={"status": "confirmed"}).status_code == 400


def test_payment_reduces_overdue(client):
    c = next(c for c in client.get("/api/customers").json()["customers"] if c["overdue_amount_pkr"] > 0)
    r = client.post(f"/api/customers/{c['customer_id']}/payments", json={"amount": c["overdue_amount_pkr"]}).json()
    assert r["facts"]["overdue_amount_pkr"] == 0


def test_ai_view_never_contains_phone_numbers(client):
    from app import db
    conn = db.connect()
    f = logic.customer_facts(conn, 2)
    assert "phone" not in logic.ai_view(f)
