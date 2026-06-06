#!/usr/bin/env bash
# PantryPilot smoke test — verify all key routes respond before a demo.
# Usage: bash scripts/smoke.sh [base_url]
#
# Exit 0 = all checks passed. Exit 1 = one or more failed.

BASE="${1:-http://localhost:8000}"
PASS=0
FAIL=0
ERRORS=()

check() {
  local label="$1"
  local method="$2"
  local url="$3"
  local expected_status="${4:-200}"
  local body_contains="${5:-}"

  if [ "$method" = "POST" ] || [ "$method" = "PUT" ] || [ "$method" = "PATCH" ]; then
    # Send empty body so Cloud Run's frontend sets Content-Length: 0
    response=$(curl -s -o /tmp/smoke_body.txt -w "%{http_code}" -X "$method" "$url" \
      -H "Accept: application/json" --data '' 2>/dev/null)
  else
    response=$(curl -s -o /tmp/smoke_body.txt -w "%{http_code}" -X "$method" "$url" \
      -H "Accept: application/json" 2>/dev/null)
  fi

  if [ "$response" != "$expected_status" ]; then
    echo "  FAIL  $label — expected HTTP $expected_status, got $response"
    FAIL=$((FAIL + 1))
    ERRORS+=("$label")
    return
  fi

  if [ -n "$body_contains" ]; then
    if ! grep -q "$body_contains" /tmp/smoke_body.txt 2>/dev/null; then
      echo "  FAIL  $label — response body missing: '$body_contains'"
      FAIL=$((FAIL + 1))
      ERRORS+=("$label")
      return
    fi
  fi

  echo "  PASS  $label"
  PASS=$((PASS + 1))
}

echo ""
echo "PantryPilot smoke test → $BASE"
echo "────────────────────────────────"

check "Home page"          GET  "$BASE/"             200  "PantryPilot"
check "Pantry rows"        GET  "$BASE/pantry-rows"  200  ""
check "Metrics (JSON)"     GET  "$BASE/metrics"      200  "pantry_count"
check "Health check"       GET  "$BASE/health"       200  ""

if [ "${SMOKE_PLAN:-0}" = "1" ]; then
  check "Plan generation"  POST "$BASE/plan?days=3"  200  "plan"
fi

echo "────────────────────────────────"
echo "  $PASS passed, $FAIL failed"
echo ""

if [ "$FAIL" -gt 0 ]; then
  echo "Failed checks: ${ERRORS[*]}"
  exit 1
fi
exit 0
