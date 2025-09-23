# Makefile для управления микросервисной архитектурой ИИ Бармена

.PHONY: help build up down restart logs clean status test

# Цвета для вывода
RED=\033[0;31m
GREEN=\033[0;32m
YELLOW=\033[1;33m
NC=\033[0m # No Color

help: ## Показать справку
	@echo "$(GREEN)Микросервисная архитектура ИИ Бармена$(NC)"
	@echo "=================================="
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | awk 'BEGIN {FS = ":.*?## "}; {printf "$(YELLOW)%-20s$(NC) %s\n", $$1, $$2}'

setup: ## Первоначальная настройка
	@echo "$(GREEN)Настройка проекта...$(NC)"
	@if [ ! -f .env ]; then \
		cp .env.microservices .env; \
		echo "$(YELLOW)Создан файл .env. Пожалуйста, заполните переменные окружения.$(NC)"; \
	fi
	@chmod +x start-microservices.sh stop-microservices.sh monitor-microservices.sh

build: ## Собрать все Docker образы
	@echo "$(GREEN)Сборка Docker образов...$(NC)"
	docker-compose -f docker-compose.microservices.yml build

build-service: ## Собрать конкретный сервис (make build-service SERVICE=gateway)
	@echo "$(GREEN)Сборка сервиса $(SERVICE)...$(NC)"
	docker-compose -f docker-compose.microservices.yml build $(SERVICE)

up: ## Запустить все микросервисы
	@echo "$(GREEN)Запуск микросервисов...$(NC)"
	docker-compose -f docker-compose.microservices.yml up -d
	@sleep 10
	@make status

down: ## Остановить все микросервисы
	@echo "$(RED)Остановка микросервисов...$(NC)"
	docker-compose -f docker-compose.microservices.yml down

restart: down up ## Перезапустить все микросервисы

restart-service: ## Перезапустить конкретный сервис (make restart-service SERVICE=gateway)
	@echo "$(GREEN)Перезапуск сервиса $(SERVICE)...$(NC)"
	docker-compose -f docker-compose.microservices.yml restart $(SERVICE)

logs: ## Показать логи всех сервисов
	docker-compose -f docker-compose.microservices.yml logs -f

logs-service: ## Показать логи конкретного сервиса (make logs-service SERVICE=gateway)
	docker-compose -f docker-compose.microservices.yml logs -f $(SERVICE)

status: ## Проверить статус всех сервисов
	@echo "$(GREEN)Статус микросервисов:$(NC)"
	@docker-compose -f docker-compose.microservices.yml ps
	@echo ""
	@./monitor-microservices.sh

health: ## Проверить здоровье системы
	@echo "$(GREEN)Проверка здоровья системы...$(NC)"
	@curl -s http://localhost:8000/health | jq . || echo "$(RED)Gateway недоступен$(NC)"

test: ## Запустить тесты API
	@echo "$(GREEN)Тестирование API...$(NC)"
	@python -m pytest tests/ -v || echo "$(YELLOW)Тесты не найдены$(NC)"

test-api: ## Быстрый тест основного API
	@echo "$(GREEN)Тестирование основного API...$(NC)"
	@curl -s -X POST http://localhost:8000/bartender/ask \
		-H "Content-Type: application/json" \
		-d '{"query": "Рецепт Мохито", "user_id": "test"}' | jq . || echo "$(RED)API недоступен$(NC)"

scale: ## Масштабировать сервисы (make scale SERVICE=rag REPLICAS=3)
	@echo "$(GREEN)Масштабирование $(SERVICE) до $(REPLICAS) реплик...$(NC)"
	docker-compose -f docker-compose.microservices.yml up -d --scale $(SERVICE)=$(REPLICAS)

clean: ## Очистить неиспользуемые Docker ресурсы
	@echo "$(RED)Очистка Docker ресурсов...$(NC)"
	docker system prune -f
	docker volume prune -f

clean-all: down ## Полная очистка (включая volumes)
	@echo "$(RED)Полная очистка...$(NC)"
	docker-compose -f docker-compose.microservices.yml down -v --remove-orphans
	docker system prune -af

backup: ## Создать бэкап векторного индекса
	@echo "$(GREEN)Создание бэкапа...$(NC)"
	@mkdir -p backups
	@tar -czf backups/vectorstore_$(shell date +%Y%m%d_%H%M%S).tar.gz vectorstore/
	@tar -czf backups/faiss_index_$(shell date +%Y%m%d_%H%M%S).tar.gz faiss_index_yandex/
	@echo "$(GREEN)Бэкап создан в папке backups/$(NC)"

restore: ## Восстановить из бэкапа (make restore BACKUP=vectorstore_20231201_120000.tar.gz)
	@echo "$(YELLOW)Восстановление из бэкапа $(BACKUP)...$(NC)"
	@tar -xzf backups/$(BACKUP) -C ./

dev: ## Запуск в режиме разработки
	@echo "$(GREEN)Запуск в режиме разработки...$(NC)"
	docker-compose -f docker-compose.microservices.yml -f docker-compose.dev.yml up

prod: ## Запуск в продакшен режиме
	@echo "$(GREEN)Запуск в продакшен режиме...$(NC)"
	docker-compose -f docker-compose.microservices.yml -f docker-compose.prod.yml up -d

install: ## Установка зависимостей для разработки
	pip install -r requirements.txt
	pip install -r requirements_api.txt
	pip install -r requirements_bot.txt

update: ## Обновление индекса
	@echo "$(GREEN)Обновление векторного индекса...$(NC)"
	@curl -s -X POST http://localhost:8002/index/update || echo "$(RED)RAG сервис недоступен$(NC)"

rebuild-index: ## Полная пересборка индекса
	@echo "$(GREEN)Пересборка векторного индекса...$(NC)"
	@curl -s -X POST http://localhost:8002/index/rebuild \
		-H "Content-Type: application/json" \
		-d '{"force": true}' || echo "$(RED)RAG сервис недоступен$(NC)"

stats: ## Показать статистику системы
	@echo "$(GREEN)Статистика системы:$(NC)"
	@echo "Gateway Health:"
	@curl -s http://localhost:8000/health | jq .
	@echo "\nLogging Stats:"
	@curl -s http://localhost:8005/stats | jq .
	@echo "\nYandex API Stats:"
	@curl -s http://localhost:8004/stats | jq .

monitor: ## Непрерывный мониторинг
	@echo "$(GREEN)Запуск мониторинга (Ctrl+C для остановки)...$(NC)"
	@while true; do \
		clear; \
		make status; \
		sleep 10; \
	done

# Полезные алиасы
start: up
stop: down
ps: status
build-all: build
