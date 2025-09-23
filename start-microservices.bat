@echo off
chcp 65001 >nul
REM Скрипт для запуска микросервисной архитектуры ИИ Бармена на Windows

echo [BARTENDER] Запуск микросервисной архитектуры ИИ Бармена

REM Проверяем наличие .env файла
if not exist .env (
    echo [ERROR] Файл .env не найден. Создаем из шаблона...
    copy .env.microservices .env
    echo [OK] Файл .env создан. Пожалуйста, заполните переменные окружения.
    pause
    exit /b 1
)

REM Проверяем наличие Docker
docker --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Docker не установлен
    pause
    exit /b 1
)

docker-compose --version >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Docker Compose не установлен
    pause
    exit /b 1
)

echo [INFO] Проверяем конфигурацию...

REM Останавливаем существующие контейнеры
echo [INFO] Останавливаем существующие контейнеры...
docker-compose -f docker-compose.microservices.yml down

REM Собираем образы
echo [BUILD] Собираем Docker образы...
docker-compose -f docker-compose.microservices.yml build

REM Запускаем сервисы
echo [START] Запускаем микросервисы...
docker-compose -f docker-compose.microservices.yml up -d

REM Ждем запуска всех сервисов
echo [WAIT] Ждем запуска всех сервисов...
timeout /t 30 /nobreak >nul

REM Проверяем статус сервисов
echo [CHECK] Проверяем статус сервисов...

set services=gateway:8000 telegram:8001 rag:8002 validation:8003 yandex:8004 logging:8005
set all_healthy=true

for %%i in (%services%) do (
    for /f "tokens=1,2 delims=:" %%a in ("%%i") do (
        echo [TEST] Проверяем %%a сервис на порту %%b...
        curl -s "http://localhost:%%b/health" >nul 2>&1
        if errorlevel 1 (
            echo [FAIL] %%a сервис недоступен (порт %%b)
            set all_healthy=false
        ) else (
            echo [OK] %%a сервис работает (порт %%b)
        )
    )
)

echo.
if "%all_healthy%"=="true" (
    echo [SUCCESS] Все микросервисы успешно запущены!
    echo.
    echo [ENDPOINTS] Доступные эндпоинты:
    echo    Gateway API:     http://localhost:8000
    echo    API Docs:        http://localhost:8000/docs
    echo    System Health:   http://localhost:8000/health
    echo    Telegram Bot:    http://localhost:8001
    echo    RAG Service:     http://localhost:8002
    echo    Validation:      http://localhost:8003
    echo    Yandex API:      http://localhost:8004
    echo    Logging:         http://localhost:8005
    echo.
    echo [INFO] Для просмотра логов: docker-compose -f docker-compose.microservices.yml logs -f
    echo [INFO] Для остановки: stop-microservices.bat
) else (
    echo.
    echo [ERROR] Некоторые сервисы не запустились. Проверьте логи:
    echo    docker-compose -f docker-compose.microservices.yml logs
)

pause
