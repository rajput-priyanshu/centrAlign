"""Seed data for the mock Vendor Mail inbox. Ground-truth invoice facts live here."""

EMAILS = [
    # Nimbus Cloud Services -- happy path target, 3 emails, agent must pick the LATEST invoice.
    {
        "id": 1,
        "sender_name": "Billing Team",
        "sender_email": "billing@nimbuscloud.example",
        "vendor_name": "Nimbus Cloud Services",
        "subject": "Invoice NCS-2041 for August usage",
        "received_at": "2026-09-02 09:14",
        "body": "Hi team,\n\nPlease find attached our invoice for August cloud usage.\n\nThanks,\nNimbus Billing",
        "has_attachment": True,
        "invoice": {
            "invoice_number": "NCS-2041",
            "amount": "4,120.00",
            "currency": "USD",
            "issue_date": "2026-09-01",
            "due_date": "2026-09-21",
        },
    },
    {
        "id": 2,
        "sender_name": "Billing Team",
        "sender_email": "billing@nimbuscloud.example",
        "vendor_name": "Nimbus Cloud Services",
        "subject": "Invoice NCS-2088 for September usage",
        "received_at": "2026-10-02 10:02",
        "body": "Hi team,\n\nPlease find attached our invoice for September cloud usage. Note our remittance address has not changed.\n\nThanks,\nNimbus Billing",
        "has_attachment": True,
        "invoice": {
            "invoice_number": "NCS-2088",
            "amount": "4,615.50",
            "currency": "USD",
            "issue_date": "2026-10-01",
            "due_date": "2026-10-21",
        },
    },
    {
        "id": 3,
        "sender_name": "Billing Team",
        "sender_email": "billing@nimbuscloud.example",
        "vendor_name": "Nimbus Cloud Services",
        "subject": "[Reminder] Invoice NCS-2088 payment due soon",
        "received_at": "2026-10-05 08:30",
        "body": "Hi team,\n\nFriendly reminder that invoice NCS-2088 is due soon. No new attachment, this is just a reminder referencing the invoice sent on 2026-10-02.\n\nThanks,\nNimbus Billing",
        "has_attachment": False,
        "invoice": None,
    },
    # Brightline Logistics -- amount is ambiguous/pending, used to exercise the clarification flow.
    {
        "id": 4,
        "sender_name": "AR Department",
        "sender_email": "ar@brightlinelogistics.example",
        "vendor_name": "Brightline Logistics",
        "subject": "Invoice BL-884 - freight charges",
        "received_at": "2026-10-03 14:20",
        "body": "Hello,\n\nAttached is our invoice for last month's freight charges. Note: the fuel surcharge line is still being finalized with carrier so the total shown is provisional.\n\nRegards,\nBrightline AR",
        "has_attachment": True,
        "invoice": {
            "invoice_number": "BL-884",
            "amount": "TBD (pending fuel surcharge reconciliation)",
            "currency": "USD",
            "issue_date": "2026-10-03",
            "due_date": "2026-10-18",
        },
    },
    # Acme Papers -- no invoices at all, used to exercise failure-detection.
]

VENDORS_WITH_NO_INVOICE = ["Acme Papers", "Acme Paper Co", "Acme"]
