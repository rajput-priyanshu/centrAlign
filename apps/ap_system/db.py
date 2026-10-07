"""SQLite storage for the mock internal Accounts Payable system.

This database is the ground truth. The orchestrator verifies task completion by
querying it directly (via the read-only /api/verify endpoint) rather than trusting
the agent's own claim that it finished.
"""
import os
import sqlite3

DB_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "data", "ap_system.db")
DB_PATH = os.path.abspath(DB_PATH)

VENDORS = [
    "Nimbus Cloud Services",
    "Brightline Logistics",
    "Acme Papers",
    "Harbor Office Supplies",
]


def get_conn():
    os.makedirs(os.path.dirname(DB_PATH), exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db(reset=False):
    conn = get_conn()
    cur = conn.cursor()
    if reset:
        cur.execute("DROP TABLE IF EXISTS vendors")
        cur.execute("DROP TABLE IF EXISTS ap_invoices")
        cur.execute("DROP TABLE IF EXISTS attempt_counts")
    cur.execute("""
        CREATE TABLE IF NOT EXISTS vendors (
            name TEXT PRIMARY KEY
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS ap_invoices (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            vendor_name TEXT NOT NULL,
            invoice_number TEXT NOT NULL,
            amount TEXT NOT NULL,
            due_date TEXT NOT NULL,
            notes TEXT,
            created_at TEXT NOT NULL,
            UNIQUE(vendor_name, invoice_number)
        )
    """)
    cur.execute("""
        CREATE TABLE IF NOT EXISTS attempt_counts (
            key TEXT PRIMARY KEY,
            count INTEGER NOT NULL DEFAULT 0
        )
    """)
    for v in VENDORS:
        cur.execute("INSERT OR IGNORE INTO vendors (name) VALUES (?)", (v,))
    conn.commit()
    conn.close()


if __name__ == "__main__":
    init_db(reset=os.environ.get("RESET_DB") == "1")
    print(f"Initialized AP system DB at {DB_PATH}")
