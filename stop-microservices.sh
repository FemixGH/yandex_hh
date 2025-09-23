#!/usr/bin/env bash
set -Eeuo pipefail

# Скрипт для остановки микросервисной архитектуры ИИ Бармена на Linux/macOS

echo "[BARTENDER] Остановка микросервисной архитектуры ИИ Бармена"

# Определяем docker compose
if docker compose version >/dev/null 2>&1; then
  COMPOSE="docker compose"
elif command -v docker-compose >/dev/null 2>&1; then
  COMPOSE="docker-compose"
else
  echo "[ERROR] Docker Compose не установлен"
  exit 1
fi

# Останавливаем и удаляем контейнеры
echo "[STOP] Останавливаю контейнеры..."
$COMPOSE -f docker-compose.microservices.yml down

# Если нужно удалить volumes, раскомментируйте следующую строку
# $COMPOSE -f docker-compose.microservices.yml down -v

echo "[OK] Все микросервисы остановлены"

# Показываем статус
echo "[STATUS] Статус Docker контейнеров:"
if docker ps -a --format '{{.Names}}' | grep -E -i '(gateway|telegram|rag|validation|yandex|logging)' >/dev/null 2>&1; then
  docker ps -a --format 'table {{.Names}}\t{{.Status}}' | grep -E -i '(gateway|telegram|rag|validation|yandex|logging)' || true
else
  echo "Нет запущенных контейнеров"
fi

