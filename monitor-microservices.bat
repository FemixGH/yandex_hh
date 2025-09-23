@echo off
chcp 65001 >nul
REM Скрипт для мониторинга состояния микросервисов на Windows

echo [MONITOR] Мониторинг микросервисной архитектуры ИИ Бармена
echo ==================================================

REM Проверяем статус контейнеров
echo.
echo [DOCKER] Статус Docker контейнеров:
docker-compose -f docker-compose.microservices.yml ps

echo.
echo [HEALTH] Проверка здоровья сервисов:

set services=gateway:8000 telegram:8001 rag:8002 validation:8003 yandex:8004 logging:8005

for %%i in (%services%) do (
    for /f "tokens=1,2 delims=:" %%a in ("%%i") do (
        echo|set /p="  %%a (%%b): "
        curl -s "http://localhost:%%b/health" >nul 2>&1
        if errorlevel 1 (
            echo [FAIL] недоступен
        ) else (
            echo [OK] работает
        )
    )
)

echo.
echo [RESOURCES] Использование ресурсов:
docker stats --no-stream --format "table {{.Name}}	{{.CPUPerc}}	{{.MemUsage}}	{{.NetIO}}" | findstr /I "gateway telegram rag validation yandex logging"

echo.
echo [LOGS] Последние логи (Gateway):
docker-compose -f docker-compose.microservices.yml logs --tail=5 gateway

pause
