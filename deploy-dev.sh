#!/bin/bash
# Скрипт для развертывания в разработке

echo "🚀 Запуск AI Bartender в режиме разработки..."

# Проверка наличия .env файла
if [ ! -f ".env" ]; then
    echo "❌ Файл .env не найден. Скопируйте .env.example в .env и заполните переменные."
    exit 1
fi

# Остановка существующих контейнеров
echo "🛑 Остановка существующих контейнеров..."
docker-compose down

# Сборка и запуск
echo "🔨 Сборка образов..."
docker-compose build

echo "▶️ Запуск сервисов..."
docker-compose up -d

# Проверка статуса
echo "📊 Проверка статуса сервисов..."
sleep 10
docker-compose ps

echo "✅ Развертывание завершено!"
echo "🌐 API доступен по адресу: http://localhost:8000"
echo "📱 Telegram бот запущен и готов к работе"
echo ""
echo "📋 Полезные команды:"
echo "  docker-compose logs -f           # Просмотр логов"
echo "  docker-compose logs -f backend   # Логи только backend"
echo "  docker-compose logs -f telegram_bot  # Логи только бота"
echo "  docker-compose down              # Остановка всех сервисов"
