@echo off
chcp 65001 >nul
setlocal

rem =============================================================
rem Follow real-time logs for local Docker Compose microservices
rem Requirements: docker-compose
rem Usage: double-click or run in terminal
rem =============================================================

echo [LOGS] Following logs for services: gateway telegram rag validation yandex logging
echo        Press Ctrl+C to stop.

docker-compose -f docker-compose.microservices.yml logs -f gateway telegram rag validation yandex logging

