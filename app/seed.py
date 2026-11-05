"""Demo ledger for a fictional agri-input shop. All names and phone numbers are made up.

Dates are relative to today so the demo always looks current. Fixed random seed = same demo every time.
"""
import random
from datetime import date, timedelta

from . import db
from .logic import now_iso

CUSTOMERS = [
    # name, village, language, crop
    ("Muhammad Aslam", "Chak 12/10-R", "roman_urdu", "wheat"),
    ("Ghulam Rasool", "Abdul Hakeem", "urdu", "cotton"),
    ("Imran Hussain", "Kabirwala", "roman_urdu", "sugarcane"),
    ("Rana Shafiq", "Mian Channu", "english", "wheat"),
    ("Abdul Majeed", "Jahanian", "urdu", "cotton"),
    ("Tariq Mehmood", "Burewala", "roman_urdu", "maize"),
    ("Sajjad Ali", "Vehari", "roman_urdu", "wheat"),
    ("Zafar Iqbal", "Chak 45/10-R", "urdu", "sugarcane"),
    ("Naeem Akhtar", "Lodhran", "roman_urdu", "cotton"),
    ("Hafiz Rehmat Ullah", "Kot Addu", "urdu", "wheat"),
    ("Shahid Nawaz", "Pir Mahal", "roman_urdu", "rice"),
    ("Khalid Pervaiz", "Tulamba", "english", "maize"),
    ("Mushtaq Ahmad", "Shorkot", "roman_urdu", "wheat"),
    ("Liaqat Ali", "Mailsi", "urdu", "cotton"),
    ("Fiaz Hussain", "Chichawatni", "roman_urdu", "sugarcane"),
    ("Arshad Mahmood", "Dunyapur", "roman_urdu", "wheat"),
    ("Bashir Ahmad", "Kabirwala", "urdu", "rice"),
    ("Riaz Ahmad", "Jahanian", "roman_urdu", "cotton"),
    ("Nasir Abbas", "Burewala", "roman_urdu", "maize"),
    ("Waqas Haider", "Vehari", "english", "wheat"),
    ("Younas Khan", "Mian Channu", "urdu", "sugarcane"),
    ("Ejaz Ahmad", "Abdul Hakeem", "roman_urdu", "wheat"),
    ("Mansha Bhatti", "Khanewal", "roman_urdu", "cotton"),
    ("Sultan Mahmood", "Lodhran", "urdu", "rice"),
]


def seed(conn, today: date | None = None, reset: bool = False) -> None:
    today = today or date.today()
    rng = random.Random(42)
    if reset:
        for t in ("ai_runs", "escalations", "promises", "reminders", "triage", "payments", "ledger", "customers"):
            conn.execute(f"DELETE FROM {t}")
        conn.execute("DELETE FROM sqlite_sequence")
    for i, (name, village, lang, crop) in enumerate(CUSTOMERS, start=1):
        conn.execute(
            "INSERT INTO customers(name, phone, village, language, crop) VALUES (?,?,?,?,?)",
            (name, f"0300{i:07d}", village, lang, crop),
        )
        cid = i
        profile = i % 6
        # Old, settled credit gives each customer a payment history.
        if profile in (0, 1, 2, 4):
            issued = today - timedelta(days=rng.randint(240, 330))
            due = issued + timedelta(days=rng.randint(60, 120))
            amount = rng.choice([18000, 25000, 40000, 55000])
            late = 0 if profile in (0, 1) else rng.randint(20, 45)
            conn.execute(
                "INSERT INTO ledger(customer_id, amount, issued_on, due_on, note) VALUES (?,?,?,?,?)",
                (cid, amount, issued.isoformat(), due.isoformat(), "Previous season inputs"),
            )
            conn.execute(
                "INSERT INTO payments(customer_id, amount, paid_on, note) VALUES (?,?,?,?)",
                (cid, amount, (due + timedelta(days=late)).isoformat(), "Settled"),
            )
        # Current season credit.
        issued = today - timedelta(days=rng.randint(70, 180))
        if profile == 5:  # not yet due
            due = today + timedelta(days=rng.randint(10, 60))
            amount = rng.choice([30000, 45000, 70000])
            paid = 0
        elif profile == 3:  # very large and long overdue
            due = today - timedelta(days=rng.randint(60, 120))
            amount = rng.choice([180000, 210000, 260000])
            paid = 0
        else:
            due = today - timedelta(days=rng.randint(5, 90))
            amount = rng.choice([22000, 35000, 48000, 65000, 90000])
            paid = rng.choice([0, 0, round(amount * 0.3, -3)])
        conn.execute(
            "INSERT INTO ledger(customer_id, amount, issued_on, due_on, note) VALUES (?,?,?,?,?)",
            (cid, amount, issued.isoformat(), due.isoformat(), f"{crop.title()} season seed, fertilizer, spray"),
        )
        if paid:
            conn.execute(
                "INSERT INTO payments(customer_id, amount, paid_on, note) VALUES (?,?,?,?)",
                (cid, paid, (today - timedelta(days=rng.randint(10, 40))).isoformat(), "Part payment"),
            )

    # One customer already promised to pay, and one was reminded yesterday, so the rules are visible in the demo.
    conn.execute(
        "INSERT INTO promises(customer_id, source_text, summary, amount, promised_date, condition, confidence, status, created_at)"
        " VALUES (?,?,?,?,?,?,?,?,?)",
        (1, "Bhai 10 din mein de dunga", "Promised to pay within 10 days.", None,
         (today + timedelta(days=8)).isoformat(), None, "high", "confirmed", now_iso()),
    )
    conn.execute(
        "INSERT INTO reminders(customer_id, language, tone, message, amount_due, amount_check_ok, status, created_at, approved_at, sent_at)"
        " VALUES (?,?,?,?,?,?,?,?,?,?)",
        (7, "roman_urdu", "friendly", "Sample earlier reminder", 0, 1, "sent", now_iso(), now_iso(),
         (today - timedelta(days=1)).isoformat() + "T09:00:00+00:00"),
    )
    conn.commit()
