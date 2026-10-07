"""Mock Vendor Mail inbox - a small real Flask web app the agent navigates with a real browser.

Not an API the agent calls directly: the agent reads rendered HTML pages, same as a human
would reading a webmail inbox, so the extraction step is genuine rather than a shortcut.
"""
import os
import sys

from dotenv import load_dotenv
from flask import Flask, render_template, request, abort, jsonify

load_dotenv(os.path.join(os.path.dirname(__file__), "..", "..", ".env"))
sys.path.insert(0, os.path.dirname(__file__))
from seed_data import EMAILS

app = Flask(__name__)


def _find_email(email_id):
    for e in EMAILS:
        if e["id"] == email_id:
            return e
    return None


@app.route("/")
def inbox():
    q = request.args.get("q", "").strip().lower()
    emails = EMAILS
    if q:
        emails = [e for e in EMAILS if q in e["vendor_name"].lower() or q in e["subject"].lower()]
    emails_sorted = sorted(emails, key=lambda e: e["received_at"], reverse=True)
    return render_template("inbox.html", emails=emails_sorted, query=q)


@app.route("/email/<int:email_id>")
def view_email(email_id):
    email = _find_email(email_id)
    if not email:
        abort(404)
    return render_template("email.html", email=email)


@app.route("/attachment/<int:email_id>")
def view_attachment(email_id):
    email = _find_email(email_id)
    if not email or not email.get("invoice"):
        abort(404)
    return render_template("attachment.html", email=email, invoice=email["invoice"])


@app.route("/api/latest_invoice")
def api_latest_invoice():
    """Read-only ground-truth oracle used ONLY by the orchestrator's independent
    verification step, never by the agent's own browser tools. It answers "what is
    actually the latest invoice for this vendor" so we can check the agent extracted
    the right figures, independent of what the agent claims it read."""
    vendor_name = request.args.get("vendor_name", "")
    candidates = [e for e in EMAILS if e["vendor_name"] == vendor_name and e.get("invoice")]
    if not candidates:
        return jsonify({"found": False})
    latest = sorted(candidates, key=lambda e: e["invoice"]["issue_date"], reverse=True)[0]
    inv = latest["invoice"]
    return jsonify({
        "found": True,
        "vendor_name": vendor_name,
        "invoice_number": inv["invoice_number"],
        "amount": inv["amount"],
        "due_date": inv["due_date"],
        "issue_date": inv["issue_date"],
    })


if __name__ == "__main__":
    port = int(os.environ.get("VENDOR_MAIL_PORT", 5001))
    app.run(host="127.0.0.1", port=port, debug=False)
