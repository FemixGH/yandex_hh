#!/usr/bin/env bash
set -Eeuo pipefail

# Скрипт для запуска микросервисной архитектуры ИИ Бармена на Linux/macOS

echo "[BARTENDER] Запуск микросервисной архитектуры ИИ Бармена"

# Проверяем наличие .env
if [[ ! -f .env ]]; then
  echo "[ERROR] Файл .env не найден. Создаю из шаблона .env.microservices..."
  if [[ -f .env.microservices ]]; then
    cp .env.microservices .env
    echo "[OK] Файл .env создан. Пожалуйста, заполните переменные окружения и запустите скрипт снова."
  else
    echo "[FAIL] Шаблон .env.microservices не найден."
  fi
  exit 1
fi

# Проверяем наличие Docker и Compose
if ! command -v docker >/dev/null 2>&1; then
  echo "[ERROR] Docker не установлен"
  exit 1
fi

if docker compose version >/dev/null 2>&1; then
  COMPOSE="docker compose"
elif command -v docker-compose >/dev/null 2>&1; then
  COMPOSE="docker-compose"
else
  echo "[ERROR] Docker Compose не установлен"
  exit 1
fi

echo "[INFO] Проверяю конфигурацию..."

# Останавливаем существующие контейнеры
echo "[INFO] Останавливаю существующие контейнеры..."
$COMPOSE -f docker-compose.microservices.yml down || true

# Собираем образы
echo "[BUILD] Собираю Docker образы..."
$COMPOSE -f docker-compose.microservices.yml build

# Запускаем сервисы
echo "[START] Запускаю микросервисы..."
$COMPOSE -f docker-compose.microservices.yml up -d

# Ждем запуска
echo "[WAIT] Жду запуска всех сервисов..."
sleep 30

# Проверяем статус сервисов
echo "[CHECK] Проверяю статус сервисов..."
services=(
  "gateway:8000"
  "telegram:8001"
  "rag:8002"
  "validation:8003"
  "yandex:8004"
  "logging:8005"
  "lockbox:8006"
)
all_healthy=1
for entry in "${services[@]}"; do
  name="${entry%%:*}"
  port="${entry##*:}"
  echo "[TEST] Проверяю ${name} сервис на порту ${port}..."
  if curl -fsS "http://localhost:${port}/health" >/dev/null 2>&1; then
    echo "[OK] ${name} сервис работает (порт ${port})"
  else
    echo "[FAIL] ${name} сервис недоступен (порт ${port})"
    all_healthy=0
  fi
done

echo
if [[ $all_healthy -eq 1 ]]; then
  echo "[SUCCESS] Все микросервисы успешно запущены!"
  echo
  echo "[ENDPOINTS] Доступные эндпоинты:"
  echo "   Gateway API:     http://localhost:8000"
  echo "   API Docs:        http://localhost:8000/docs"
  echo "   System Health:   http://localhost:8000/health"
  echo "   Telegram Bot:    http://localhost:8001"
  echo "   RAG Service:     http://localhost:8002"
  echo "   Validation:      http://localhost:8003"
  echo "   Yandex API:      http://localhost:8004"
  echo "   Logging:         http://localhost:8005"
  echo "   Lockbox:         http://localhost:8006"
  echo
  echo "[INFO] Для просмотра логов: $COMPOSE -f docker-compose.microservices.yml logs -f"
  echo "[INFO] Для остановки: ./stop-microservices.sh"
else
  echo
  echo "[ERROR] Некоторые сервисы не запустились. Проверьте логи:"
  echo "   $COMPOSE -f docker-compose.microservices.yml logs"
  exit 1
fi
