#!/usr/bin/env bash
# Launches all three processes needed for the prototype:
#   1. Vendor Mail      (mock inbox the agent reads invoices from)   -> :5001
#   2. Internal AP System (mock internal app the agent enters data into) -> :5002
#   3. Dashboard        (submit tasks / watch the agent / verify)    -> :5000
set -e
cd "$(dirname "$0")"

if [ ! -f .env ]; then
  echo "No .env found. Copy .env.example to .env and add your ANTHROPIC_API_KEY first."
  exit 1
fi

mkdir -p data evidence

cleanup() {
  echo "Shutting down..."
  kill $(jobs -p) 2>/dev/null
}
trap cleanup EXIT INT TERM

python3 apps/vendor_mail/app.py &
python3 apps/ap_system/app.py &
sleep 1
python3 dashboard/app.py &

echo ""
echo "Dashboard:        http://127.0.0.1:${DASHBOARD_PORT:-5000}"
echo "Vendor Mail:      http://127.0.0.1:${VENDOR_MAIL_PORT:-5001}"
echo "Internal AP Sys:  http://127.0.0.1:${AP_SYSTEM_PORT:-5002}"
echo ""

wait
