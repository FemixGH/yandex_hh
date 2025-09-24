#!/usr/bin/env bash
set -euo pipefail

# =============================================================
# Full Test for Yandex Cloud Serverless Containers
# - Checks health of all services
# - Performs functional tests on key endpoints
# Requires: yc CLI, jq, curl
# =============================================================

# --- Utils & Config ---
command -v yc >/dev/null 2>&1 || { echo "[ERROR] yc CLI not found. Install and run 'yc init'."; exit 1; }
command -v jq >/dev/null 2>&1 || { echo "[ERROR] jq not found. Install jq."; exit 1; }
command -v curl >/dev/null 2>&1 || { echo "[ERROR] curl not found. Install curl."; exit 1; }

SERVICES=(gateway telegram rag validation yandex logging lockbox)
declare -A URLS

# --- Helper Functions ---
_print_header() {
  echo
  echo "================================================="
  echo "  $1"
  echo "================================================="
}

_print_status() {
  local status=$1
  local message=$2
  if [[ "$status" == "OK" ]]; then
    echo "    [✅ OK] $message"
  else
    echo "    [❌ FAIL] $message"
  fi
}

# --- 1. Resolve Service URLs ---
_print_header "1. Resolving Service URLs"
for SVC in "${SERVICES[@]}"; do
  if URL_JSON=$(yc serverless container get --name "$SVC" --format json 2>/dev/null || true); then
    RAW_URL=$(echo "$URL_JSON" | jq -r '.url // empty')
    URLS[$SVC]=${RAW_URL%/} # Trim trailing slash
  else
    URLS[$SVC]=
  fi
  printf "  %-10s = %s\n" "$SVC" "${URLS[$SVC]:-(not found)}"
done

# --- 2. Health Checks ---
_print_header "2. Health Checks (/health)"
for SVC in "${SERVICES[@]}"; do
  URL=${URLS[$SVC]:-}
  if [[ -n "$URL" ]]; then
    printf "  %-10s: " "$SVC"
    if curl -sS --max-time 10 "$URL/health" >/dev/null; then
      _print_status "OK" "$URL/health"
    else
      _print_status "FAIL" "$URL/health"
    fi
  else
    printf "  %-10s: [SKIP] URL not found\n" "$SVC"
  fi
done

# --- 3. Gateway Functional Test ---
_print_header "3. Gateway: Round-trip Test (/bartender/ask)"
if [[ -n "${URLS[gateway]:-}" ]]; then
  # Test 1: Мохито рецепт
  echo "  Testing Мохито recipe..."
  RESP=$(curl -sS -m 20 -X POST -H 'Content-Type: application/json' \
    -d '{"query":"рецепт Мохито","user_id":"full-test-script"}' \
    "${URLS[gateway]}/bartender/ask" || true)

  if echo "$RESP" | jq -e '.answer' >/dev/null 2>&1; then
    _print_status "OK" "Мохито: Response contains 'answer' field."
    echo "      Answer preview: $(echo "$RESP" | jq -r '.answer' | head -c 100)..."
  elif echo "$RESP" | jq -e '.blocked' >/dev/null 2>&1; then
    _print_status "OK" "Мохито: Response contains 'blocked' field (moderation)."
  else
    _print_status "FAIL" "Мохито: Unexpected or invalid JSON response."
    echo "      Response: $RESP"
  fi

  # Test 2: Лимонад рецепт
  echo "  Testing лимонад recipe..."
  LEMONADE_RESP=$(curl -sS -m 20 -X POST -H 'Content-Type: application/json' \
    -d '{"query":"как сделать лимонад","user_id":"full-test-script-lemonade"}' \
    "${URLS[gateway]}/bartender/ask" || true)

  if echo "$LEMONADE_RESP" | jq -e '.answer' >/dev/null 2>&1; then
    _print_status "OK" "Лимонад: Response contains 'answer' field."
    echo "      Answer preview: $(echo "$LEMONADE_RESP" | jq -r '.answer' | head -c 100)..."
  elif echo "$LEMONADE_RESP" | jq -e '.blocked' >/dev/null 2>&1; then
    _print_status "OK" "Лимонад: Response contains 'blocked' field (moderation)."
  else
    _print_status "FAIL" "Лимонад: Unexpected or invalid JSON response."
    echo "      Response: $LEMONADE_RESP"
  fi
else
  _print_status "FAIL" "Gateway URL not found."
fi

# --- 4. RAG Service Tests ---
_print_header "4. RAG: Index Status & Search"
if [[ -n "${URLS[rag]:-}" ]]; then
  # Test 1: Index Status
  STATUS_RESP=$(curl -sS -m 10 "${URLS[rag]}/index/status" || true)
  if echo "$STATUS_RESP" | jq -e '.exists == true and .documents_count > 0' >/dev/null 2>&1; then
    _print_status "OK" "Index exists and is not empty."
  else
    _print_status "FAIL" "Index does not exist or is empty."
    echo "      Response: $STATUS_RESP"
  fi

  # Test 2: Semantic Search
  SEARCH_RESP=$(curl -sS -m 15 -X POST -H 'Content-Type: application/json' \
    -d '{"query":"виски","k":1}' \
    "${URLS[rag]}/search" || true)
  if echo "$SEARCH_RESP" | jq -e '.total_found > 0' >/dev/null 2>&1; then
    _print_status "OK" "Semantic search returned results."
  else
    _print_status "FAIL" "Semantic search returned no results or failed."
    echo "      Response: $SEARCH_RESP"
  fi
else
    _print_status "FAIL" "RAG URL not found."
fi

# --- 5. Validation Service Test ---
_print_header "5. Validation: Moderation Test (/moderate)"
if [[ -n "${URLS[validation]:-}" ]]; then
  # Test 1: Safe text
  SAFE_RESP=$(curl -sS -m 10 -X POST -H 'Content-Type: application/json' \
    -d '{"text":"привет","is_input":true}' \
    "${URLS[validation]}/moderate" || true)
  if echo "$SAFE_RESP" | jq -e '.is_safe == true' >/dev/null 2>&1; then
    _print_status "OK" "Safe text passed moderation."
  else
    _print_status "FAIL" "Safe text failed moderation."
    echo "      Response: $SAFE_RESP"
  fi
else
    _print_status "FAIL" "Validation URL not found."
fi

# --- 6. Yandex API Service Test ---
_print_header "6. Yandex: Embedding Test (/embedding)"
if [[ -n "${URLS[yandex]:-}" ]]; then
  EMB_RESP=$(curl -sS -m 15 -X POST -H 'Content-Type: application/json' \
    -d '{"text":"test"}' \
    "${URLS[yandex]}/embedding" || true)
  if echo "$EMB_RESP" | jq -e '.embedding | length > 0' >/dev/null 2>&1; then
    _print_status "OK" "Successfully generated text embedding."
  else
    _print_status "FAIL" "Failed to generate text embedding."
    echo "      Response: $EMB_RESP"
  fi
else
    _print_status "FAIL" "Yandex service URL not found."
fi

# --- 7. Logging Service Test ---
_print_header "7. Logging: Log & Stats Test"
if [[ -n "${URLS[logging]:-}" ]]; then
  LOG_RESP=$(curl -sS -m 10 -X POST -H 'Content-Type: application/json' \
    -d '{"level":"INFO","message":"Full test script check","service":"test-runner"}' \
    "${URLS[logging]}/log" || true)
  if echo "$LOG_RESP" | jq -e '.success == true' >/dev/null 2>&1; then
    _print_status "OK" "Log entry created successfully."
  else
    _print_status "FAIL" "Failed to create log entry."
    echo "      Response: $LOG_RESP"
  fi
else
    _print_status "FAIL" "Logging service URL not found."
fi

# --- 8. Telegram Webhook Status ---
_print_header "8. Telegram: Webhook Status"
if [[ -n "${URLS[telegram]:-}" ]]; then
  INFO=$(curl -sS -m 10 "${URLS[telegram]}/webhook/info" || true)
  if echo "$INFO" | jq -e '.telegram.url // empty | type == "string"' >/dev/null 2>&1; then
    USE_WH=$(echo "$INFO" | jq -r '.use_webhook')
    CFG_URL=$(echo "$INFO" | jq -r '.configured_webhook_url // empty')
    TG_URL=$(echo "$INFO" | jq -r '.telegram.url // empty')
    PENDING=$(echo "$INFO" | jq -r '.telegram.pending_update_count // 0')

    echo "  use_webhook            : $USE_WH"
    echo "  configured_webhook_url : ${CFG_URL:-"(not set)"}"
    echo "  telegram.url           : ${TG_URL:-"(empty)"}"
    echo "  pending_update_count   : $PENDING"

    EXPECTED_URL="${URLS[gateway]}/telegram/webhook"
    if [[ "$USE_WH" == "true" ]]; then
      if [[ -z "$TG_URL" || "$TG_URL" != "$EXPECTED_URL" || -z "$CFG_URL" || "$CFG_URL" != "$EXPECTED_URL" ]]; then
        echo "  Detected mismatch or empty webhook URL. Trying to sync to: $EXPECTED_URL"
        SYNC_RESP=$(curl -sS -m 15 -X POST "${URLS[telegram]}/webhook/sync" || true)
        if echo "$SYNC_RESP" | jq -e '.success == true' >/dev/null 2>&1; then
          _print_status "OK" "Webhook re-synced successfully."
        else
          _print_status "FAIL" "Failed to sync webhook."
          echo "      Response: $SYNC_RESP"
        fi
      else
        _print_status "OK" "Webhook is set correctly."
      fi
    else
      echo "  Webhook mode is disabled (polling)."
    fi
  else
    _print_status "FAIL" "Failed to get webhook info."
    echo "      Response: $INFO"
  fi
else
  _print_status "FAIL" "Telegram URL not found."
fi

echo
echo "====== Test run finished. ======"
