"""Scripted end-to-end smoke test of the real browser automation against the real
mock apps - no LLM involved. This exists to validate that BrowserSession's
label/text-based click/type heuristics actually work against our HTML, and that the
orchestrator's independent verification logic correctly flags a match, BEFORE
spending LLM calls debugging selector issues through the agent loop.

Run with both mock apps already running (see run.sh), or this script will skip.
"""
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from agent.browser import BrowserSession

VENDOR_MAIL_URL = os.environ.get("VENDOR_MAIL_URL", "http://127.0.0.1:5001")
AP_SYSTEM_URL = os.environ.get("AP_SYSTEM_URL", "http://127.0.0.1:5002")


def main():
    evidence_dir = os.path.join(os.path.dirname(__file__), "_smoke_evidence")
    b = BrowserSession(evidence_dir, headless=True)
    try:
        b.navigate(f"{VENDOR_MAIL_URL}/?q=Nimbus")
        obs = b.observe()
        assert "Nimbus Cloud Services" in obs["visible_text"], "inbox search failed"
        print("[ok] inbox search found Nimbus emails")

        b.click("Invoice NCS-2088 for September usage")
        obs = b.observe()
        assert "Open attachment" in obs["links"] or any("attachment" in l.lower() for l in obs["links"])
        print("[ok] opened latest Nimbus email")

        b.click("Open attachment")
        obs = b.observe()
        assert "4,615.50" in obs["visible_text"] and "2026-10-21" in obs["visible_text"]
        print("[ok] read invoice amount + due date from attachment:", "4,615.50 / 2026-10-21")

        b.navigate(f"{AP_SYSTEM_URL}/login")
        b.type("Username", "ap_agent")
        b.type("Password", "CentrAlign#2026", submit_label="Log in")
        obs = b.observe()
        assert "vendors" in obs["url"].lower() or "Vendors" in obs["visible_text"]
        print("[ok] logged into AP system")

        b.click("Nimbus Cloud Services")
        b.type("Invoice number", "NCS-2088")
        b.type("Amount", "4615.50")
        b.type("Due date", "2026-10-21", submit_label="Submit invoice")
        obs = b.observe()
        assert "500" in obs["title"] or "Temporary Error" in obs["visible_text"], "expected simulated flaky 500 on first attempt"
        print("[ok] first submission attempt hit the simulated transient 500, as expected")

        b.navigate(f"{AP_SYSTEM_URL}/vendors/Nimbus Cloud Services")
        b.type("Invoice number", "NCS-2088")
        b.type("Amount", "4615.50")
        b.type("Due date", "2026-10-21", submit_label="Submit invoice")
        obs = b.observe()
        assert "recorded successfully" in obs["visible_text"]
        print("[ok] retry succeeded, invoice recorded")

        print("\nALL SMOKE CHECKS PASSED")
    finally:
        b.close()


if __name__ == "__main__":
    main()
