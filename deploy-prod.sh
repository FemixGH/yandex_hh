#!/bin/bash
# Скрипт для развертывания в продакшне

echo "🚀 Запуск AI Bartender в продакшн режиме..."

# Проверка наличия .env.production файла
if [ ! -f ".env.production" ]; then
    echo "❌ Файл .env.production не найден. Создайте файл с продакшн переменными."
    exit 1
fi

# Остановка существующих контейнеров
echo "🛑 Остановка существующих контейнеров..."
docker-compose -f docker-compose.prod.yml down

# Очистка старых образов (опционально)
read -p "🗑️ Удалить старые образы? (y/N): " -n 1 -r
echo
if [[ $REPLY =~ ^[Yy]$ ]]; then
    docker system prune -f
fi

# Сборка и запуск
echo "🔨 Сборка продакшн образов..."
docker-compose -f docker-compose.prod.yml build --no-cache

echo "▶️ Запуск продакшн сервисов..."
docker-compose -f docker-compose.prod.yml up -d

# Ожидание запуска сервисов
echo "⏳ Ожидание запуска сервисов..."
sleep 30

# Проверка статуса
echo "📊 Проверка статуса сервисов..."
docker-compose -f docker-compose.prod.yml ps

# Health check
echo "🏥 Проверка здоровья сервисов..."
curl -f http://localhost:8000/health || echo "⚠️ Backend недоступен"

echo "✅ Продакшн развертывание завершено!"
echo "🌐 API доступен по адресу: http://localhost:8000"
echo "📊 Prometheus мониторинг: http://localhost:9090"
echo "📱 Telegram бот запущен"
echo ""
echo "📋 Полезные команды:"
echo "  docker-compose -f docker-compose.prod.yml logs -f  # Просмотр логов"
echo "  docker-compose -f docker-compose.prod.yml down     # Остановка сервисов"
echo "  docker-compose -f docker-compose.prod.yml restart  # Перезапуск сервисов"
