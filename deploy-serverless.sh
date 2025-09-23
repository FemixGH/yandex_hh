#!/usr/bin/env bash
set -Eeuo pipefail

# Yandex Cloud Serverless deploy for all services (Linux/macOS)
# Requires: yc CLI (yc init), docker, jq

# --------- dependencies ---------
command -v yc >/dev/null 2>&1 || { echo "[ERROR] yc CLI не найден. Установите и выполните 'yc init'."; exit 1; }
command -v docker >/dev/null 2>&1 || { echo "[ERROR] docker не найден. Установите Docker и выполните вход."; exit 1; }
command -v jq >/dev/null 2>&1 || { echo "[ERROR] jq не найден. Установите jq (https://stedolan.github.io/jq/)."; exit 1; }

# --------- config ---------
FOLDER_ID=${FOLDER_ID:-$(yc config get folder-id || true)}
CLOUD_ID=${CLOUD_ID:-$(yc config get cloud-id || true)}
if [[ -z "${FOLDER_ID:-}" ]]; then
  echo "[ERROR] folder-id не установлен. Выполните 'yc init' или 'yc config set folder-id <id>'."
  exit 1
fi

echo "[INFO] Folder: ${FOLDER_ID}  Cloud: ${CLOUD_ID:-unknown}"

# --------- Service Account ---------
SA_NAME=${SA_NAME:-sc-containers}
SA_ID="$(yc iam service-account get --name "${SA_NAME}" --format json 2>/dev/null | jq -r '.id // empty')"
if [[ -z "${SA_ID}" ]]; then
  echo "[INFO] Создаю сервисный аккаунт ${SA_NAME}..."
  SA_ID="$(yc iam service-account create --name "${SA_NAME}" --format json | jq -r '.id')"
else
  echo "[INFO] Использую сервисный аккаунт ${SA_NAME}: ${SA_ID}"
fi
if [[ -z "${SA_ID}" ]]; then
  echo "[ERROR] Не удалось получить id сервисного аккаунта."
  exit 1
fi

# --------- Container Registry ---------
yc container registry configure-docker >/dev/null 2>&1 || true
REGISTRY_ID="$(yc container registry list --format json | jq -r '.[0].id // empty')"
if [[ -z "${REGISTRY_ID}" ]]; then
  echo "[INFO] Создаю Container Registry yandex-hh-reg..."
  yc container registry create --name yandex-hh-reg >/dev/null
  REGISTRY_ID="$(yc container registry list --format json | jq -r '.[0].id')"
fi
REGISTRY="cr.yandex/${REGISTRY_ID}"
echo "[INFO] Registry: ${REGISTRY}"

# --------- Access bindings (idempotent) ---------
yc container registry add-access-binding --id "${REGISTRY_ID}" --role container-registry.images.puller --subject "serviceAccount:${SA_ID}" >/dev/null 2>&1 || true
yc resource-manager folder add-access-binding --id "${FOLDER_ID}" --role ai.languageModels.user --subject "serviceAccount:${SA_ID}" >/dev/null 2>&1 || true

# Lockbox secret access (измените при необходимости)
SECRET_ID=${SECRET_ID:-e6q8vbbldqor67ogn9ne}
yc lockbox secret add-access-binding --id "${SECRET_ID}" --role lockbox.payloadViewer --subject "serviceAccount:${SA_ID}" >/dev/null 2>&1 || true

# --------- Optional envs ---------
: "${S3_BUCKET:=vedroo}"
: "${S3_PREFIX:=}"
: "${S3_ENDPOINT:=https://storage.yandexcloud.net}"
: "${TELEGRAM_TOKEN:=${TELEGRAM_TOKEN:-}}"
if [[ -z "${TELEGRAM_TOKEN}" ]]; then
  read -r -p "Enter TELEGRAM_TOKEN (или оставьте пустым, чтобы пропустить деплой Telegram): " TELEGRAM_TOKEN || true
fi
if [[ -z "${S3_ACCESS_KEY:-}" ]]; then read -r -p "Enter S3_ACCESS_KEY (опционально): " S3_ACCESS_KEY || true; fi
if [[ -z "${S3_SECRET_KEY:-}" ]]; then read -r -p "Enter S3_SECRET_KEY (опционально): " S3_SECRET_KEY || true; fi

echo "[INFO] Использую S3: bucket=${S3_BUCKET} prefix=${S3_PREFIX} endpoint=${S3_ENDPOINT}"

# --------- Build & Push images ---------
set -o pipefail
build_push() {
  local name=$1
  local dockerfile=$2
  echo "[BUILD] ${name} ..."
  docker build -t "${REGISTRY}/${name}:latest" -f "${dockerfile}" .
  echo "[PUSH] ${name} ..."
  docker push "${REGISTRY}/${name}:latest"
}

build_push lockbox    services/lockbox/Dockerfile
build_push logging    services/logging/Dockerfile
build_push validation services/validation/Dockerfile
build_push yandex     services/yandex/Dockerfile
build_push rag        services/rag/Dockerfile
build_push gateway    services/gateway/Dockerfile
build_push telegram   services/telegram/Dockerfile

# --------- Ensure containers exist ---------
for SVC in lockbox logging validation yandex rag gateway telegram; do
  yc serverless container get --name "${SVC}" >/dev/null 2>&1 || yc serverless container create --name "${SVC}" >/dev/null
done

# --------- Deploy backends ---------

echo "[DEPLOY] lockbox"
yc serverless container revision deploy \
  --container-name lockbox \
  --image "${REGISTRY}/lockbox:latest" \
  --service-account-id "${SA_ID}" \
  --cores 1 --memory 256MB --concurrency 16 --execution-timeout 10s \
  --environment LOCKBOX_SERVICE_HOST=0.0.0.0,LOCKBOX_SERVICE_PORT=8080,SECRET_ID="${SECRET_ID}" >/dev/null

yc serverless container allow-unauthenticated-invoke --name lockbox >/dev/null 2>&1 || true


echo "[DEPLOY] logging"
yc serverless container revision deploy \
  --container-name logging \
  --image "${REGISTRY}/logging:latest" \
  --service-account-id "${SA_ID}" \
  --cores 1 --memory 256MB --concurrency 16 --execution-timeout 10s \
  --environment LOGGING_SERVICE_HOST=0.0.0.0,LOGGING_SERVICE_PORT=8080 >/dev/null

yc serverless container allow-unauthenticated-invoke --name logging >/dev/null 2>&1 || true


echo "[DEPLOY] validation"
yc serverless container revision deploy \
  --container-name validation \
  --image "${REGISTRY}/validation:latest" \
  --service-account-id "${SA_ID}" \
  --cores 1 --memory 256MB --concurrency 16 --execution-timeout 10s \
  --environment VALIDATION_SERVICE_HOST=0.0.0.0,VALIDATION_SERVICE_PORT=8080,FOLDER_ID="${FOLDER_ID}" >/dev/null

yc serverless container allow-unauthenticated-invoke --name validation >/dev/null 2>&1 || true


echo "[DEPLOY] yandex"
yc serverless container revision deploy \
  --container-name yandex \
  --image "${REGISTRY}/yandex:latest" \
  --service-account-id "${SA_ID}" \
  --cores 1 --memory 512MB --concurrency 16 --execution-timeout 15s \
  --environment YANDEX_SERVICE_HOST=0.0.0.0,YANDEX_SERVICE_PORT=8080,FOLDER_ID="${FOLDER_ID}" >/dev/null

yc serverless container allow-unauthenticated-invoke --name yandex >/dev/null 2>&1 || true


echo "[DEPLOY] rag"
RAG_ENV=(
  "RAG_SERVICE_HOST=0.0.0.0"
  "RAG_SERVICE_PORT=8080"
  "VECTORSTORE_DIR=/tmp/vectorstore"
  "FOLDER_ID=${FOLDER_ID}"
  "S3_ENDPOINT=${S3_ENDPOINT}"
  "S3_BUCKET=${S3_BUCKET}"
  "S3_PREFIX=${S3_PREFIX}"
)
[[ -n "${S3_ACCESS_KEY:-}" ]] && RAG_ENV+=("S3_ACCESS_KEY=${S3_ACCESS_KEY}")
[[ -n "${S3_SECRET_KEY:-}" ]] && RAG_ENV+=("S3_SECRET_KEY=${S3_SECRET_KEY}")

# join env with commas
RAG_ENV_JOINED=$(IFS=, ; echo "${RAG_ENV[*]}")

yc serverless container revision deploy \
  --container-name rag \
  --image "${REGISTRY}/rag:latest" \
  --service-account-id "${SA_ID}" \
  --cores 2 --memory 1024MB --concurrency 8 --execution-timeout 60s \
  --environment "${RAG_ENV_JOINED}" >/dev/null

yc serverless container allow-unauthenticated-invoke --name rag >/dev/null 2>&1 || true

# --------- Read URLs ---------
LOCKBOX_URL="$(yc serverless container get --name lockbox --format json | jq -r '.url')"
LOGGING_URL="$(yc serverless container get --name logging --format json | jq -r '.url')"
VALIDATION_URL="$(yc serverless container get --name validation --format json | jq -r '.url')"
YANDEX_URL="$(yc serverless container get --name yandex --format json | jq -r '.url')"
RAG_URL="$(yc serverless container get --name rag --format json | jq -r '.url')"

echo "[INFO] URLs:"
echo "  LOCKBOX_URL    = ${LOCKBOX_URL}"
echo "  LOGGING_URL    = ${LOGGING_URL}"
echo "  VALIDATION_URL = ${VALIDATION_URL}"
echo "  YANDEX_URL     = ${YANDEX_URL}"
echo "  RAG_URL        = ${RAG_URL}"

# --------- Deploy gateway ---------

echo "[DEPLOY] gateway (initial)"
yc serverless container revision deploy \
  --container-name gateway \
  --image "${REGISTRY}/gateway:latest" \
  --service-account-id "${SA_ID}" \
  --cores 1 --memory 512MB --concurrency 16 --execution-timeout 15s \
  --environment GATEWAY_HOST=0.0.0.0,GATEWAY_PORT=8080,LOCKBOX_SERVICE_URL="${LOCKBOX_URL}",LOGGING_SERVICE_URL="${LOGGING_URL}",VALIDATION_SERVICE_URL="${VALIDATION_URL}",YANDEX_SERVICE_URL="${YANDEX_URL}",RAG_SERVICE_URL="${RAG_URL}" >/dev/null

yc serverless container allow-unauthenticated-invoke --name gateway >/dev/null 2>&1 || true
GATEWAY_URL="$(yc serverless container get --name gateway --format json | jq -r '.url')"
echo "[INFO] GATEWAY_URL = ${GATEWAY_URL}"

# --------- Deploy telegram (optional) ---------
if [[ -n "${TELEGRAM_TOKEN}" ]]; then
  echo "[DEPLOY] telegram"
  yc serverless container revision deploy \
    --container-name telegram \
    --image "${REGISTRY}/telegram:latest" \
    --service-account-id "${SA_ID}" \
    --cores 1 --memory 512MB --concurrency 4 --execution-timeout 300s \
    --environment TELEGRAM_SERVICE_HOST=0.0.0.0,TELEGRAM_SERVICE_PORT=8080,TELEGRAM_TOKEN="${TELEGRAM_TOKEN}",GATEWAY_URL="${GATEWAY_URL}" >/dev/null
  yc serverless container allow-unauthenticated-invoke --name telegram >/dev/null 2>&1 || true
  TELEGRAM_URL="$(yc serverless container get --name telegram --format json | jq -r '.url')"
  echo "[INFO] TELEGRAM_URL = ${TELEGRAM_URL}"
  echo "[DEPLOY] gateway (update with TELEGRAM url)"
  yc serverless container revision deploy \
    --container-name gateway \
    --image "${REGISTRY}/gateway:latest" \
    --service-account-id "${SA_ID}" \
    --cores 1 --memory 512MB --concurrency 16 --execution-timeout 15s \
    --environment GATEWAY_HOST=0.0.0.0,GATEWAY_PORT=8080,LOCKBOX_SERVICE_URL="${LOCKBOX_URL}",LOGGING_SERVICE_URL="${LOGGING_URL}",VALIDATION_SERVICE_URL="${VALIDATION_URL}",YANDEX_SERVICE_URL="${YANDEX_URL}",RAG_SERVICE_URL="${RAG_URL}",TELEGRAM_SERVICE_URL="${TELEGRAM_URL}" >/dev/null
fi

# --------- Summary ---------
echo
echo "================= Deployment completed ================="
echo "GATEWAY_URL    = ${GATEWAY_URL}"
echo "LOCKBOX_URL    = ${LOCKBOX_URL}"
echo "LOGGING_URL    = ${LOGGING_URL}"
echo "VALIDATION_URL = ${VALIDATION_URL}"
echo "YANDEX_URL     = ${YANDEX_URL}"
echo "RAG_URL        = ${RAG_URL}"
[[ -n "${TELEGRAM_URL:-}" ]] && echo "TELEGRAM_URL   = ${TELEGRAM_URL}"
echo "========================================================="
echo "Use:  curl \"${GATEWAY_URL}/health\""
echo "      curl \"${GATEWAY_URL}/lockbox/secret/${SECRET_ID}/kv\""
echo

