from flask import Flask, redirect, request, render_template
import stripe
import sqlite3
import os

# ✅ Stripe key from environment ONLY
stripe.api_key = os.getenv("STRIPE_KEY")

if not stripe.api_key:
    raise ValueError("STRIPE_KEY is not set")

app = Flask(__name__)

# ---------------- DB SETUP ----------------
def init_db():
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

# 📊 Count sold
def get_total_sold():
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
        success_url="http://YOUR_PUBLIC_IP/success",
        cancel_url="http://YOUR_PUBLIC_IP/",
    )

    return redirect(session.url)

@app.route("/success")
def success():
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

# ---------------- RUN ----------------

if __name__ == "__main__":
    init_db()
    app.run(host="0.0.0.0", port=5001, debug=True)