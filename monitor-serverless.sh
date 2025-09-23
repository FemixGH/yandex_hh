#!/usr/bin/env bash
set -euo pipefail

# =============================================================
# Monitor Yandex Cloud Serverless Containers (Linux/macOS)
# - Checks health of all services
# - Optionally tails real-time logs for all services
# Requires: yc CLI (yc init), jq, curl
# Usage:
#   ./monitor-serverless.sh           # just health checks
#   ./monitor-serverless.sh --logs    # health + follow logs
# =============================================================

command -v yc >/dev/null 2>&1 || { echo "[ERROR] yc CLI not found. Install and run 'yc init'."; exit 1; }
command -v jq >/dev/null 2>&1 || { echo "[ERROR] jq not found. Install jq."; exit 1; }
command -v curl >/dev/null 2>&1 || { echo "[ERROR] curl not found."; exit 1; }

FOLLOW_LOGS=false
if [[ "${1:-}" == "--logs" ]]; then
  FOLLOW_LOGS=true
fi

SERVICES=(gateway telegram rag validation yandex logging lockbox)

declare -A URLS

echo "[INFO] Resolving service URLs from Serverless Containers..."
for SVC in "${SERVICES[@]}"; do
  if URL_JSON=$(yc serverless container get --name "$SVC" --format json 2>/dev/null || true); then
    RAW_URL=$(echo "$URL_JSON" | jq -r '.url // empty')
    # Trim trailing slash if present
    if [[ -n "$RAW_URL" ]]; then
      RAW_URL=${RAW_URL%/}
    fi
    URLS[$SVC]="$RAW_URL"
  else
    URLS[$SVC]=
  fi
  printf "  %-10s = %s\n" "$SVC" "${URLS[$SVC]:-(not found)}"
done

echo
echo "[HEALTH] Checking /health endpoints (10s timeout):"
for SVC in "${SERVICES[@]}"; do
  URL=${URLS[$SVC]:-}
  if [[ -n "$URL" ]]; then
    printf "  %-10s : %s/health\n" "$SVC" "$URL"
    if curl -sS --max-time 10 "$URL/health" >/dev/null; then
      echo "    [OK]"
    else
      echo "    [FAIL]"
    fi
  else
    printf "  %-10s : [SKIP] URL not found\n" "$SVC"
  fi
done

if [[ -n "${URLS[gateway]:-}" ]]; then
  echo
  echo "[TEST] Round-trip via Gateway /bartender/ask:"
  RESP=$(curl -sS -m 15 -X POST -H 'Content-Type: application/json' -d '{"query":"рецепт Мохито","user_id":"selftest"}' "${URLS[gateway]}/bartender/ask" || true)
  if echo "$RESP" | jq . >/dev/null 2>&1; then
    if echo "$RESP" | grep -qE '"answer"|"blocked"'; then
      echo "  [OK] request processed"
    else
      echo "  [WARN] unexpected payload:"; echo "$RESP"
    fi
  else
    echo "  [FAIL] invalid response:"; echo "$RESP"
  fi
fi

if $FOLLOW_LOGS; then
  echo
  echo "[LOGS] Following logs (since 10m). Press Ctrl+C to stop."
  for SVC in "${SERVICES[@]}"; do
    {
      yc serverless container logs read --name "$SVC" --follow --since 10m \
      | sed -u "s/^/[$SVC] /"
    } &
    PIDS+="$! "
  done
  trap 'echo; echo "[LOGS] Stopping..."; kill $PIDS >/dev/null 2>&1 || true; wait >/dev/null 2>&1 || true' INT TERM
  wait
fi
