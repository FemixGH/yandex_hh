#!/usr/bin/env bash
set -euo pipefail

# =============================================================
# Audit Yandex Cloud Serverless stack:
# - Runs full functional tests (reuses ./full-test-serverless.sh)
# - Optionally prints ALL key=value pairs from Lockbox secret
#   identified by SECRET_ID (dangerous; disabled by default)
#
# Requires: yc CLI (yc init), jq, curl
# Usage examples:
#   ./audit-serverless.sh                          # run tests only
#   ./audit-serverless.sh --print-secrets --yes    # tests + print secrets from $SECRET_ID
#   ./audit-serverless.sh --print-secrets --secret-id <id> --yes
#   ./audit-serverless.sh --logs                   # tests + follow logs (delegated)
# =============================================================

# ---- Args ----
PRINT_SECRETS=false
SECRET_ID_ARG=""
VERSION_ID_ARG=""
FOLLOW_LOGS=false
ASSUME_YES=false

while [[ $# -gt 0 ]]; do
  case "$1" in
    --print-secrets)
      PRINT_SECRETS=true
      shift
      ;;
    --secret-id)
      SECRET_ID_ARG=${2:-}
      shift 2
      ;;
    --version-id)
      VERSION_ID_ARG=${2:-}
      shift 2
      ;;
    --logs)
      FOLLOW_LOGS=true
      shift
      ;;
    --yes|--assume-yes)
      ASSUME_YES=true
      shift
      ;;
    -h|--help)
      cat <<EOF
Usage: $0 [options]

Options:
  --print-secrets           Output all key=value entries from Lockbox secret
  --secret-id <id>          Lockbox SECRET_ID to read (defaults to $SECRET_ID or autodetect)
  --version-id <id>         Optional secret version to read
  --logs                    After tests, follow live logs for all services
  --yes                     Skip interactive confirmation for printing secrets
  -h, --help                Show this help
EOF
      exit 0
      ;;
    *)
      echo "[WARN] Unknown arg: $1" >&2
      shift
      ;;
  esac
done

# ---- Dependencies ----
need() { command -v "$1" >/dev/null 2>&1 || { echo "[ERROR] $1 not found."; exit 1; }; }
need yc
need jq
need curl

# ---- Run existing full tests ----
if [[ -x ./full-test-serverless.sh ]]; then
  if $FOLLOW_LOGS; then
    echo "[INFO] Running full tests, then will follow logs..."
  else
    echo "[INFO] Running full tests..."
  fi
  ./full-test-serverless.sh
else
  echo "[WARN] ./full-test-serverless.sh not found or not executable. Running inline basic health checks."
  SERVICES=(gateway telegram rag validation yandex logging lockbox)
  declare -A URLS
  echo "[INFO] Resolving service URLs from Serverless Containers..."
  for SVC in "${SERVICES[@]}"; do
    if URL_JSON=$(yc serverless container get --name "$SVC" --format json 2>/dev/null || true); then
      RAW_URL=$(echo "$URL_JSON" | jq -r '.url // empty')
      RAW_URL=${RAW_URL%/}
      URLS[$SVC]="$RAW_URL"
    else
      URLS[$SVC]=''
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
fi

# ---- Optionally follow logs (reuse monitor script) ----
if $FOLLOW_LOGS; then
  if [[ -x ./monitor-serverless.sh ]]; then
    echo
    echo "[INFO] Following logs via monitor-serverless.sh..."
    ./monitor-serverless.sh --logs
  else
    echo "[WARN] monitor-serverless.sh not found; skipping logs follow."
  fi
fi

# ---- Secrets printing logic ----
if $PRINT_SECRETS; then
  echo
  echo "================= DANGEROUS OPERATION ================="
  echo "You are about to PRINT ALL SECRETS (key=value) from Lockbox."
  echo "Anyone with access to this terminal history/screen will see them."
  echo "This is intended only for controlled debugging."
  echo "======================================================="

  if ! $ASSUME_YES; then
    # If interactive TTY, ask for confirmation; otherwise require --yes
    if [[ -t 0 ]]; then
      read -r -p "Type YES to confirm reveal: " CONFIRM || true
      if [[ "$CONFIRM" != "YES" ]]; then
        echo "[ABORT] Secrets reveal cancelled."
        exit 2
      fi
    else
      echo "[ERROR] Non-interactive shell. Pass --yes to proceed with secrets printing."
      exit 2
    fi
  fi

  # Resolve SECRET_ID to use
  SECRET_ID=${SECRET_ID_ARG:-${SECRET_ID:-}}

  discover_secret_id() {
    local id=""
    # Try to find SECRET_ID from latest gateway/telegram/rag/validation/yandex revisions
    local CANDIDATES=(gateway telegram rag validation yandex logging lockbox)
    for SVC in "${CANDIDATES[@]}"; do
      # Latest revision id
      local REV
      REV=$(yc serverless container revision list --container-name "$SVC" --limit 1 --format json 2>/dev/null | \
            jq -r '.[0].id // empty') || true
      if [[ -n "$REV" ]]; then
        # Try to read environment from revision (map or array formats)
        id=$(yc serverless container revision get --id "$REV" --format json 2>/dev/null | \
             jq -r '.environment.SECRET_ID // (.environment[]? | select(.key=="SECRET_ID") | .value) // empty') || true
        if [[ -n "$id" ]]; then
          echo "$id"
          return 0
        fi
      fi
    done
    # Fallback: show the first available Lockbox secret (not ideal, but helpful)
    id=$(yc lockbox secret list --format json 2>/dev/null | jq -r '.[0].id // empty') || true
    echo "$id"
  }

  if [[ -z "${SECRET_ID:-}" ]]; then
    SECRET_ID=$(discover_secret_id)
  fi

  if [[ -z "${SECRET_ID:-}" ]]; then
    echo "[ERROR] SECRET_ID is not set and could not be auto-discovered."
    echo "        Set env SECRET_ID or pass --secret-id <id>."
    exit 3
  fi

  echo "[INFO] Using SECRET_ID: $SECRET_ID${VERSION_ID_ARG:+  version: $VERSION_ID_ARG}"

  # Print payload via yc CLI in a robust way (handles text_value/textValue)
  if PAYLOAD=$(yc lockbox payload get --id "$SECRET_ID" ${VERSION_ID_ARG:+--version-id "$VERSION_ID_ARG"} --format json 2>/dev/null); then
    echo
    echo "----- Lockbox payload -----"
    echo "$PAYLOAD" | jq -r '.entries[] | "\(.key)=\(.text_value // .textValue // "")"'
    echo "---------------------------"
  else
    echo "[ERROR] Failed to fetch Lockbox payload. Check permissions (lockbox.payloadViewer) and SECRET_ID."
    exit 4
  fi
fi

echo
echo "[DONE] Audit complete."

