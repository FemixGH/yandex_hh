#!/usr/bin/env bash
set -Eeuo pipefail

# Скрипт для мониторинга состояния микросервисов на Linux/macOS

echo "[MONITOR] Мониторинг микросервисной архитектуры ИИ Бармена"
echo "=================================================="

# Определяем docker compose
if docker compose version >/dev/null 2>&1; then
  COMPOSE="docker compose"
elif command -v docker-compose >/dev/null 2>&1; then
  COMPOSE="docker-compose"
else
  echo "[ERROR] Docker Compose не установлен"
  exit 1
fi

echo
echo "[DOCKER] Статус Docker контейнеров:"
$COMPOSE -f docker-compose.microservices.yml ps

echo
echo "[HEALTH] Проверка здоровья сервисов:"
services=(
  "gateway:8000"
  "telegram:8001"
  "rag:8002"
  "validation:8003"
  "yandex:8004"
  "logging:8005"
  "lockbox:8006"
)
for entry in "${services[@]}"; do
  name="${entry%%:*}"
  port="${entry##*:}"
  printf "  %s (%s): " "$name" "$port"
  if curl -fsS "http://localhost:${port}/health" >/dev/null 2>&1; then
    echo "[OK] работает"
  else
    echo "[FAIL] недоступен"
  fi
done

echo
echo "[RESOURCES] Использование ресурсов:"
# Печатаем таблицу только по интересующим контейнерам, если docker stats доступен
if docker stats --no-stream >/dev/null 2>&1; then
  docker stats --no-stream --format 'table {{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.NetIO}}' | \
    grep -E '(gateway|telegram|rag|validation|yandex|logging|lockbox)' || true
else
  echo "docker stats недоступен"
fi

echo
echo "[LOGS] Последние логи (Gateway):"
$COMPOSE -f docker-compose.microservices.yml logs --tail=5 gateway || true

