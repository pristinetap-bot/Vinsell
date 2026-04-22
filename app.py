from flask import Flask, redirect, request, render_template, abort
import stripe
import sqlite3
import os

stripe.api_key = os.getenv("STRIPE_SECRET_KEY") or os.getenv("STRIPE_KEY")

app = Flask(__name__)


def get_base_url():
    base_url = os.getenv("BASE_URL")
    if base_url:
        return base_url.rstrip("/")
    return request.url_root.rstrip("/")


def require_stripe_key():
    if not stripe.api_key:
        abort(
            500,
            description=(
                "Stripe is not configured. Set STRIPE_SECRET_KEY "
                "(or STRIPE_KEY) before starting the app."
            ),
        )

# ---------------- DB SETUP ----------------
def init_db():
    conn = sqlite3.connect("links.db")
    c = conn.cursor()
    c.execute("""
    CREATE TABLE IF NOT EXISTS links (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        url TEXT,
        status TEXT DEFAULT 'unused',
        stripe_session_id TEXT
    )
    """)
    c.execute("PRAGMA table_info(links)")
    columns = [row[1] for row in c.fetchall()]
    if "stripe_session_id" not in columns:
        c.execute("ALTER TABLE links ADD COLUMN stripe_session_id TEXT")
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
    require_stripe_key()

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
        success_url=f"{get_base_url()}/success?session_id={{CHECKOUT_SESSION_ID}}",
        cancel_url=f"{get_base_url()}/",
    )

    return redirect(session.url)

# ✅ Assign link AFTER payment → instant redirect
@app.route("/success")
def success():
    require_stripe_key()

    session_id = request.args.get("session_id")
    if not session_id:
        return "<h2>Payment session not found.</h2>", 400

    try:
        session = stripe.checkout.Session.retrieve(session_id)
    except stripe.error.StripeError:
        return "<h2>Unable to verify payment with Stripe.</h2>", 400

    if session.get("payment_status") != "paid":
        return "<h2>Payment was not completed.</h2>", 400

    conn = sqlite3.connect("links.db")
    conn.isolation_level = "EXCLUSIVE"
    c = conn.cursor()

    c.execute("BEGIN EXCLUSIVE")

    c.execute(
        "SELECT url FROM links WHERE stripe_session_id=? AND status='used' LIMIT 1",
        (session_id,),
    )
    existing = c.fetchone()
    if existing:
        conn.commit()
        conn.close()
        return redirect(existing[0])

    c.execute("SELECT id, url FROM links WHERE status='unused' LIMIT 1")
    row = c.fetchone()

    if not row:
        conn.commit()
        conn.close()
        return "<h2>❌ Sold Out.</h2>"

    link_id, link = row

    # mark as used
    c.execute(
        "UPDATE links SET status='used', stripe_session_id=? WHERE id=?",
        (session_id, link_id),
    )
    conn.commit()
    conn.close()

    # 🚀 DIRECT redirect (no page)
    return redirect(link)

# ---------------- RUN ----------------

if __name__ == "__main__":
    init_db()
    seed_links()
    app.run(host="0.0.0.0", port=5001, debug=True)
