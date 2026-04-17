from flask import Flask, redirect, request, render_template
import stripe
import sqlite3
import os

# 🔑 Stripe key (replace with your real key)
stripe.api_key = os.getenv("STRIPE_KEY") or "sk_test_XXXX"

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

def seed_links():
    conn = sqlite3.connect("links.db")
    c = conn.cursor()

    c.execute("SELECT COUNT(*) FROM links")
    if c.fetchone()[0] == 0:
        links = [
            ("https://vinchaxun.com/?CODE1",),
            ("https://vinchaxun.com/?CODE2",),
            ("https://vinchaxun.com/?CODE3",),
            ("https://example.com/report1",),
            ("https://example.com/report2",),
        ]
        c.executemany("INSERT INTO links (url) VALUES (?)", links)
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

# ✅ Check availability before payment
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
                "unit_amount": 600,  # $6
            },
            "quantity": 1,
        }],
        success_url="http://127.0.0.1:5001/success",
        cancel_url="http://127.0.0.1:5001/",
    )

    return redirect(session.url)

# ✅ Assign link AFTER payment → instant redirect
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

    # mark as used
    c.execute("UPDATE links SET status='used' WHERE id=?", (link_id,))
    conn.commit()
    conn.close()

    # 🚀 DIRECT redirect (no page)
    return redirect(link)

# ---------------- RUN ----------------

if __name__ == "__main__":
    init_db()
    seed_links()
    app.run(host="0.0.0.0", port=5001, debug=True)