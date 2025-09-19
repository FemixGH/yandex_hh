# Makefile для управления AI Bartender контейнерами

.PHONY: help build up down logs restart clean dev prod

# Переменные
COMPOSE_FILE=docker-compose.yml
PROD_COMPOSE_FILE=docker-compose.prod.yml
PROJECT_NAME=ai-bartender

help: ## Показать справку
	@echo "Доступные команды:"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-20s\033[0m %s\n", $$1, $$2}'

build: ## Собрать все образы
	docker-compose -f $(COMPOSE_FILE) build

build-prod: ## Собрать продакшн образы
	docker-compose -f $(PROD_COMPOSE_FILE) build --no-cache

dev: ## Запустить в режиме разработки
	@echo "🚀 Запуск в режиме разработки..."
	docker-compose -f $(COMPOSE_FILE) up -d
	@echo "✅ Сервисы запущены!"
	@echo "🌐 API: http://localhost:8000"

prod: ## Запустить в продакшн режиме
	@echo "🚀 Запуск в продакшн режиме..."
	docker-compose -f $(PROD_COMPOSE_FILE) up -d
	@echo "✅ Продакшн сервисы запущены!"
	@echo "🌐 API: http://localhost:8000"
	@echo "📊 Мониторинг: http://localhost:9090"

up: dev ## Запустить (алиас для dev)

down: ## Остановить все сервисы
	docker-compose -f $(COMPOSE_FILE) down
	docker-compose -f $(PROD_COMPOSE_FILE) down

logs: ## Показать логи всех сервисов
	docker-compose -f $(COMPOSE_FILE) logs -f

logs-backend: ## Показать логи backend
	docker-compose -f $(COMPOSE_FILE) logs -f backend

logs-bot: ## Показать логи telegram bot
	docker-compose -f $(COMPOSE_FILE) logs -f telegram_bot

status: ## Показать статус сервисов
	docker-compose -f $(COMPOSE_FILE) ps

restart: ## Перезапустить все сервисы
	docker-compose -f $(COMPOSE_FILE) restart

restart-backend: ## Перезапустить backend
	docker-compose -f $(COMPOSE_FILE) restart backend

restart-bot: ## Перезапустить telegram bot
	docker-compose -f $(COMPOSE_FILE) restart telegram_bot

clean: ## Очистить неиспользуемые образы и контейнеры
	docker system prune -f
	docker volume prune -f

clean-all: ## Полная очистка (ОСТОРОЖНО!)
	docker-compose -f $(COMPOSE_FILE) down -v
	docker-compose -f $(PROD_COMPOSE_FILE) down -v
	docker system prune -af
	docker volume prune -f

health: ## Проверить здоровье сервисов
	@echo "🏥 Проверка здоровья сервисов..."
	@curl -f http://localhost:8000/health || echo "❌ Backend недоступен"

shell-backend: ## Подключиться к backend контейнеру
	docker-compose -f $(COMPOSE_FILE) exec backend bash

shell-bot: ## Подключиться к bot контейнеру
	docker-compose -f $(COMPOSE_FILE) exec telegram_bot bash

update: ## Обновить и перезапустить
	git pull
	docker-compose -f $(COMPOSE_FILE) pull
	docker-compose -f $(COMPOSE_FILE) up -d --build

# Команды для облачного развертывания
push-images: ## Отправить образы в реестр
	@echo "📤 Отправка образов в реестр..."
	docker tag $(PROJECT_NAME)_backend:latest your-registry/$(PROJECT_NAME)-backend:latest
	docker tag $(PROJECT_NAME)_telegram_bot:latest your-registry/$(PROJECT_NAME)-bot:latest
	docker push your-registry/$(PROJECT_NAME)-backend:latest
	docker push your-registry/$(PROJECT_NAME)-bot:latest

# Мониторинг
monitor: ## Показать использование ресурсов
	docker stats

backup: ## Создать backup данных
	@echo "💾 Создание backup..."
	docker run --rm -v ai-bartender_vectorstore_data:/data -v $(PWD):/backup alpine tar czf /backup/vectorstore-backup-$(shell date +%Y%m%d-%H%M%S).tar.gz -C /data .
	docker run --rm -v ai-bartender_faiss_data:/data -v $(PWD):/backup alpine tar czf /backup/faiss-backup-$(shell date +%Y%m%d-%H%M%S).tar.gz -C /data .
