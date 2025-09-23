@echo off
chcp 65001 >nul
REM Скрипт для остановки микросервисной архитектуры ИИ Бармена на Windows

echo [BARTENDER] Остановка микросервисной архитектуры ИИ Бармена

REM Останавливаем и удаляем контейнеры
echo [STOP] Останавливаем контейнеры...
docker-compose -f docker-compose.microservices.yml down

REM Опционально удаляем volumes (раскомментируйте если нужно)
REM echo [CLEAN] Удаляем volumes...
REM docker-compose -f docker-compose.microservices.yml down -v

echo [OK] Все микросервисы остановлены

REM Показываем статус
echo [STATUS] Статус Docker контейнеров:
docker ps -a | findstr /I "gateway telegram rag validation yandex logging" || echo Нет запущенных контейнеров

pause
