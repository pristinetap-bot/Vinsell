from flask import Flask, redirect, request, render_template
import os
import sqlite3
import stripe

stripe.api_key = os.getenv("STRIPE_KEY")
if not stripe.api_key:
    raise ValueError("STRIPE_KEY is not set")

app = Flask(__name__)


# ---------------- DB SETUP ----------------
def init_db() -> None:
    conn = sqlite3.connect("links.db")
    c = conn.cursor()
    c.execute("""
    CREATE TABLE IF NOT EXISTS links (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        url TEXT,
        status TEXT DEFAULT 'unused'
    )
    """)
    conn.commit()
    conn.close()


init_db()


# ---------------- HELPERS ----------------
def get_total_sold() -> int:
    conn = sqlite3.connect("links.db")
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM links WHERE status='used'")
    count = c.fetchone()[0]
    conn.close()
    return count


# ---------------- ROUTES ----------------
@app.route("/")
def home():
    base = 385
    total_sold = base + get_total_sold()
    return render_template("index.html", total_sold=total_sold)


@app.route("/buy", methods=["POST"])
def buy():
    conn = sqlite3.connect("links.db")
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM links WHERE status='unused'")
    available = c.fetchone()[0]
    conn.close()

    if available == 0:
        return "<h2>❌ Sold Out. No reports available.</h2>"

    try:
        session = stripe.checkout.Session.create(
            payment_method_types=["card"],
            mode="payment",
            line_items=[{
                "price_data": {
                    "currency": "usd",
                    "product_data": {
                        "name": "Vehicle History Report"
                    },
                    "unit_amount": 600,
                },
                "quantity": 1,
            }],
            success_url="https://clearvinreport.org/success?session_id={CHECKOUT_SESSION_ID}",
            cancel_url="https://clearvinreport.org/",
        )
    except Exception as e:
        return f"<h2>Payment setup error: {str(e)}</h2>", 500

    return redirect(session.url)


@app.route("/success")
def success():
    session_id = request.args.get("session_id")
    if not session_id:
        return "Invalid access", 403

    try:
        session = stripe.checkout.Session.retrieve(session_id)
    except Exception:
        return "Invalid session", 403

    if session.payment_status != "paid":
        return "Payment not completed", 403

    conn = sqlite3.connect("links.db")
    conn.isolation_level = "EXCLUSIVE"
    c = conn.cursor()

    c.execute("BEGIN EXCLUSIVE")
    c.execute("SELECT id, url FROM links WHERE status='unused' LIMIT 1")
    row = c.fetchone()

    if not row:
        conn.commit()
        conn.close()
        return "<h2>❌ Sold Out.</h2>"

    link_id, link = row

    c.execute("UPDATE links SET status='used' WHERE id=?", (link_id,))
    conn.commit()
    conn.close()

    return redirect(link)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001)