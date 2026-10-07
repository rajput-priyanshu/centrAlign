"""Mock internal Accounts Payable system - a real Flask app with login, forms, and a
real SQLite database. It also deliberately fails the FIRST submission attempt for any
given invoice with a transient 500 error, so the agent must genuinely detect a failure
and retry - this is not scripted into the agent, it is a property of the environment.
"""
import datetime
import os
import sys

from dotenv import load_dotenv
from flask import Flask, render_template, request, redirect, url_for, session, abort, jsonify

load_dotenv(os.path.join(os.path.dirname(__file__), "..", "..", ".env"))
sys.path.insert(0, os.path.dirname(__file__))
from db import get_conn, init_db

app = Flask(__name__)
app.secret_key = os.environ.get("AP_SYSTEM_SECRET", "dev-secret-not-for-production")

AP_USERNAME = os.environ.get("AP_SYSTEM_USERNAME", "ap_agent")
AP_PASSWORD = os.environ.get("AP_SYSTEM_PASSWORD", "CentrAlign#2026")


def logged_in():
    return session.get("logged_in") is True


@app.route("/")
def home():
    if not logged_in():
        return redirect(url_for("login"))
    return redirect(url_for("vendor_list"))


@app.route("/login", methods=["GET", "POST"])
def login():
    error = None
    if request.method == "POST":
        username = request.form.get("username", "")
        password = request.form.get("password", "")
        if username == AP_USERNAME and password == AP_PASSWORD:
            session["logged_in"] = True
            return redirect(url_for("vendor_list"))
        error = "Invalid username or password."
    return render_template("login.html", error=error)


@app.route("/logout")
def logout():
    session.clear()
    return redirect(url_for("login"))


@app.route("/vendors")
def vendor_list():
    if not logged_in():
        return redirect(url_for("login"))
    q = request.args.get("q", "").strip().lower()
    conn = get_conn()
    rows = conn.execute("SELECT name FROM vendors ORDER BY name").fetchall()
    conn.close()
    vendors = [r["name"] for r in rows]
    if q:
        vendors = [v for v in vendors if q in v.lower()]
    return render_template("vendor_list.html", vendors=vendors, query=q)


@app.route("/vendors/<vendor_name>", methods=["GET"])
def vendor_detail(vendor_name):
    if not logged_in():
        return redirect(url_for("login"))
    conn = get_conn()
    vendor = conn.execute("SELECT name FROM vendors WHERE name = ?", (vendor_name,)).fetchone()
    if not vendor:
        conn.close()
        abort(404)
    invoices = conn.execute(
        "SELECT * FROM ap_invoices WHERE vendor_name = ? ORDER BY created_at DESC",
        (vendor_name,),
    ).fetchall()
    conn.close()
    return render_template("vendor_detail.html", vendor_name=vendor_name, invoices=invoices, error=None)


@app.route("/vendors/<vendor_name>/invoices", methods=["POST"])
def submit_invoice(vendor_name):
    if not logged_in():
        return redirect(url_for("login"))

    invoice_number = request.form.get("invoice_number", "").strip()
    amount = request.form.get("amount", "").strip()
    due_date = request.form.get("due_date", "").strip()
    notes = request.form.get("notes", "").strip()

    conn = get_conn()
    vendor = conn.execute("SELECT name FROM vendors WHERE name = ?", (vendor_name,)).fetchone()
    if not vendor:
        conn.close()
        abort(404)

    if not invoice_number or not amount or not due_date:
        invoices = conn.execute(
            "SELECT * FROM ap_invoices WHERE vendor_name = ? ORDER BY created_at DESC",
            (vendor_name,),
        ).fetchall()
        conn.close()
        return render_template(
            "vendor_detail.html", vendor_name=vendor_name, invoices=invoices,
            error="Invoice number, amount, and due date are all required fields.",
        ), 400

    existing = conn.execute(
        "SELECT * FROM ap_invoices WHERE vendor_name = ? AND invoice_number = ?",
        (vendor_name, invoice_number),
    ).fetchone()
    if existing:
        conn.close()
        return redirect(url_for("vendor_detail", vendor_name=vendor_name, already_exists=1))

    key = f"{vendor_name}|{invoice_number}"
    row = conn.execute("SELECT count FROM attempt_counts WHERE key = ?", (key,)).fetchone()
    attempt_count = row["count"] if row else 0

    if attempt_count == 0:
        if row:
            conn.execute("UPDATE attempt_counts SET count = 1 WHERE key = ?", (key,))
        else:
            conn.execute("INSERT INTO attempt_counts (key, count) VALUES (?, 1)", (key,))
        conn.commit()
        conn.close()
        return render_template(
            "error_500.html",
            message="Upstream ledger service timed out while saving this invoice. Please retry.",
        ), 500

    conn.execute(
        "INSERT INTO ap_invoices (vendor_name, invoice_number, amount, due_date, notes, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (vendor_name, invoice_number, amount, due_date, notes, datetime.datetime.now().isoformat(timespec="seconds")),
    )
    conn.commit()
    conn.close()
    return redirect(url_for("vendor_detail", vendor_name=vendor_name, submitted=1))


@app.route("/api/verify")
def api_verify():
    """Read-only ground-truth check used by the orchestrator, independent of the agent."""
    vendor_name = request.args.get("vendor_name", "")
    invoice_number = request.args.get("invoice_number", "")
    conn = get_conn()
    row = conn.execute(
        "SELECT * FROM ap_invoices WHERE vendor_name = ? AND invoice_number = ?",
        (vendor_name, invoice_number),
    ).fetchone()
    conn.close()
    if not row:
        return jsonify({"found": False})
    return jsonify({
        "found": True,
        "vendor_name": row["vendor_name"],
        "invoice_number": row["invoice_number"],
        "amount": row["amount"],
        "due_date": row["due_date"],
        "notes": row["notes"],
        "created_at": row["created_at"],
    })


if __name__ == "__main__":
    init_db(reset=os.environ.get("RESET_DB") == "1")
    port = int(os.environ.get("AP_SYSTEM_PORT", 5002))
    app.run(host="127.0.0.1", port=port, debug=False)
