from flask import (
    Flask,
    abort,
    jsonify,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
import json
import os
import sqlite3
import stripe
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import urlopen

# ---------------- APP SETUP ----------------
app = Flask(__name__)
app.secret_key = os.getenv("FLASK_SECRET_KEY", "change-this-admin-secret")

# ---------------- STRIPE SETUP ----------------
stripe.api_key = os.getenv("STRIPE_SECRET_KEY") or os.getenv("STRIPE_KEY")

LOW_STOCK_THRESHOLD = int(os.getenv("LOW_STOCK_THRESHOLD", "3"))


def get_base_url():
    base_url = os.getenv("BASE_URL")
    if base_url:
        return base_url.rstrip("/")
    return request.url_root.rstrip("/")


def get_db_connection():
    conn = sqlite3.connect("links.db")
    conn.row_factory = sqlite3.Row
    return conn


def require_stripe_key():
    if not stripe.api_key:
        abort(
            500,
            description=(
                "Stripe is not configured. Set STRIPE_SECRET_KEY "
                "(or STRIPE_KEY) before starting the app."
            ),
        )


def get_admin_password():
    return os.getenv("ADMIN_PASSWORD")


def is_admin_logged_in():
    return session.get("is_admin") is True


def require_admin():
    if not get_admin_password():
        abort(
            500,
            description="Admin is not configured. Set ADMIN_PASSWORD before using /admin.",
        )
    if not is_admin_logged_in():
        return redirect(url_for("admin_login"))
    return None


def normalize_vin(vin):
    return "".join((vin or "").upper().split())


def decode_vin(vin):
    normalized_vin = normalize_vin(vin)

    if len(normalized_vin) != 17:
        return {"ok": False, "message": "VIN must be 17 characters."}

    if any(char in normalized_vin for char in ("I", "O", "Q")):
        return {"ok": False, "message": 'VIN cannot contain the letters "I", "O", or "Q".'}

    api_url = (
        "https://vpic.nhtsa.dot.gov/api/vehicles/DecodeVinValues/"
        f"{normalized_vin}?format=json"
    )

    try:
        with urlopen(api_url, timeout=10) as response:
            payload = json.loads(response.read().decode("utf-8"))
    except Exception:
        return {"ok": False, "message": "Unable to verify VIN right now. Please try again."}

    results = payload.get("Results") or []
    if not results:
        return {"ok": False, "message": "VIN could not be decoded."}

    record = results[0]
    make = (record.get("Make") or "").strip()
    model = (record.get("Model") or "").strip()
    year = (record.get("ModelYear") or "").strip()
    error_code = (record.get("ErrorCode") or "").strip()
    error_text = (record.get("ErrorText") or "").strip()

    if error_code and error_code != "0":
        return {
            "ok": False,
            "message": error_text or "VIN could not be decoded.",
        }

    if not any([make, model, year]):
        return {"ok": False, "message": "VIN could not be verified."}

    vehicle_parts = [part for part in [year, make, model] if part]
    vehicle_label = " ".join(vehicle_parts)

    return {
        "ok": True,
        "vin": normalized_vin,
        "year": year,
        "make": make,
        "model": model,
        "vehicle_label": vehicle_label,
    }


def build_forward_url(base_url, vin):
    query_param = os.getenv("FORWARD_VIN_QUERY_PARAM", "").strip()
    if not query_param or not vin:
        return base_url

    split_url = urlsplit(base_url)
    query = dict(parse_qsl(split_url.query, keep_blank_values=True))
    query[query_param] = vin

    return urlunsplit(
        (
            split_url.scheme,
            split_url.netloc,
            split_url.path,
            urlencode(query),
            split_url.fragment,
        )
    )


# ---------------- DB SETUP ----------------
def init_db():
    conn = get_db_connection()
    c = conn.cursor()
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS links (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            url TEXT,
            status TEXT DEFAULT 'unused',
            stripe_session_id TEXT
        )
        """
    )
    c.execute(
        """
        CREATE TABLE IF NOT EXISTS settings (
            key TEXT PRIMARY KEY,
            value TEXT
        )
        """
    )
    c.execute("PRAGMA table_info(links)")
    columns = [row[1] for row in c.fetchall()]
    if "stripe_session_id" not in columns:
        c.execute("ALTER TABLE links ADD COLUMN stripe_session_id TEXT")
    conn.commit()
    conn.close()


init_db()


# ---------------- HELPERS ----------------
def get_total_sold():
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM links WHERE status='used'")
    count = c.fetchone()[0]
    conn.close()
    return count


def get_available_links_count():
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM links WHERE status='unused'")
    count = c.fetchone()[0]
    conn.close()
    return count


def get_admin_dashboard_data():
    conn = get_db_connection()
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM links WHERE status='unused'")
    available = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM links WHERE status='used'")
    used = c.fetchone()[0]
    c.execute("SELECT COUNT(*) FROM links")
    total = c.fetchone()[0]
    c.execute(
        """
        SELECT id, url, status
        FROM links
        ORDER BY id DESC
        LIMIT 12
        """
    )
    recent_links = c.fetchall()
    conn.close()
    return {
        "available": available,
        "used": used,
        "total": total,
        "recent_links": recent_links,
        "low_stock_threshold": LOW_STOCK_THRESHOLD,
    }


# ---------------- ROUTES ----------------
@app.route("/")
def home():
    base = 385
    total_sold = base + get_total_sold()
    return render_template("index.html", total_sold=total_sold)


@app.route("/api/decode-vin", methods=["POST"])
def api_decode_vin():
    payload = request.get_json(silent=True) or {}
    result = decode_vin(payload.get("vin", ""))
    status_code = 200 if result["ok"] else 400
    return jsonify(result), status_code


@app.route("/buy", methods=["POST"])
def buy():
    require_stripe_key()

    available = get_available_links_count()
    if available == 0:
        return "<h2>❌ Sold Out. No reports available.</h2>"

    vin_result = decode_vin(request.form.get("vin", ""))
    if not vin_result["ok"]:
        return f"<h2>{vin_result['message']}</h2>", 400

    metadata = {
        "type": "vin_report",
        "vin": vin_result["vin"],
        "vehicle": vin_result["vehicle_label"],
    }

    try:
        session_data = stripe.checkout.Session.create(
            mode="payment",
            payment_method_types=["card"],
            line_items=[
                {
                    "price_data": {
                        "currency": "usd",
                        "product_data": {"name": "Vehicle History Report"},
                        "unit_amount": 500,
                    },
                    "quantity": 1,
                }
            ],
            billing_address_collection="auto",
            metadata=metadata,
            success_url=f"{get_base_url()}/success?session_id={{CHECKOUT_SESSION_ID}}",
            cancel_url=f"{get_base_url()}/",
        )
    except Exception as e:
        return f"<h2>Payment setup error: {str(e)}</h2>", 500

    return redirect(session_data.url)


@app.route("/success")
def success():
    require_stripe_key()

    session_id = request.args.get("session_id")
    if not session_id:
        return "Invalid access", 403

    try:
        stripe_session = stripe.checkout.Session.retrieve(session_id)
    except Exception as e:
        return f"Invalid session: {str(e)}", 403

    if stripe_session["payment_status"] != "paid":
        return "Payment not completed", 403

    session_metadata = stripe_session["metadata"] if "metadata" in stripe_session else None
    vin = session_metadata["vin"] if session_metadata and "vin" in session_metadata else ""

    conn = get_db_connection()
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
        return redirect(build_forward_url(existing["url"], vin))

    c.execute("SELECT id, url FROM links WHERE status='unused' ORDER BY id LIMIT 1")
    row = c.fetchone()

    if not row:
        conn.commit()
        conn.close()
        return "<h2>❌ Sold Out.</h2>"

    c.execute(
        "UPDATE links SET status='used', stripe_session_id=? WHERE id=?",
        (session_id, row["id"]),
    )
    conn.commit()
    conn.close()

    return redirect(build_forward_url(row["url"], vin))


@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():
    if not get_admin_password():
        abort(
            500,
            description="Admin is not configured. Set ADMIN_PASSWORD before using /admin.",
        )

    error = None
    if request.method == "POST":
        password = request.form.get("password", "")
        if password == get_admin_password():
            session["is_admin"] = True
            return redirect(url_for("admin"))
        error = "Incorrect password."

    return render_template("admin_login.html", error=error)


@app.route("/admin/logout", methods=["POST"])
def admin_logout():
    session.clear()
    return redirect(url_for("admin_login"))


@app.route("/admin")
def admin():
    redirect_response = require_admin()
    if redirect_response:
        return redirect_response

    message = request.args.get("message")
    dashboard = get_admin_dashboard_data()
    return render_template("admin.html", message=message, **dashboard)


@app.route("/admin/add-links", methods=["POST"])
def admin_add_links():
    redirect_response = require_admin()
    if redirect_response:
        return redirect_response

    raw_links = request.form.get("links", "")
    lines = [line.strip() for line in raw_links.splitlines()]
    cleaned_links = [line for line in lines if line]

    if not cleaned_links:
        return redirect(url_for("admin", message="Paste at least one link."))

    conn = get_db_connection()
    c = conn.cursor()
    c.executemany(
        "INSERT INTO links (url, status, stripe_session_id) VALUES (?, 'unused', NULL)",
        [(link,) for link in cleaned_links],
    )
    conn.commit()
    conn.close()

    return redirect(
        url_for("admin", message=f"Added {len(cleaned_links)} new link(s).")
    )


# ---------------- RUN ----------------
if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5001, debug=True)
