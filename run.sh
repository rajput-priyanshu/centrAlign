#!/usr/bin/env bash
# Launches all three processes needed for the prototype:
#   1. Vendor Mail         (mock inbox the agent reads invoices from)      -> :5001
#   2. Internal AP System  (mock internal app the agent enters data into)  -> :5002
#   3. Dashboard           (submit tasks / watch the agent / verify)       -> :5050
set -e
cd "$(dirname "$0")"

if [ ! -f .env ]; then
  echo "No .env found. Copy .env.example to .env and add your GEMINI_API_KEY first."
  exit 1
fi

# Preserve a command-line override (e.g. `RESET_DB=1 ./run.sh`) over .env's value.
_RESET_DB_OVERRIDE="${RESET_DB-}"
set -a
source .env
set +a
if [ -n "$_RESET_DB_OVERRIDE" ]; then
  export RESET_DB="$_RESET_DB_OVERRIDE"
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
echo "Dashboard:        http://127.0.0.1:${DASHBOARD_PORT:-5050}"
echo "Vendor Mail:      http://127.0.0.1:${VENDOR_MAIL_PORT:-5001}"
echo "Internal AP Sys:  http://127.0.0.1:${AP_SYSTEM_PORT:-5002}"
echo ""

wait
