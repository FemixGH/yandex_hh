# AI Bartender - Контейнеризация и Развертывание

## 🏗️ Архитектура системы

Проект состоит из следующих контейнеризированных сервисов:

- **Backend API** (FastAPI) - основной API сервер
- **Telegram Bot** - телеграм бот для взаимодействия с пользователями
- **Redis** - кеширование и сессии
- **Nginx** - обратный прокси (продакшн)
- **Prometheus** - мониторинг (продакшн)

## 🚀 Быстрый старт

### 1. Подготовка окружения

```bash
# Клонируйте репозиторий
git clone <your-repo>
cd yandex_hh

# Создайте файл переменных окружения
cp .env.example .env
# Отредактируйте .env файл со своими значениями
```

### 2. Развертывание для разработки

```bash
# Сделайте скрипт исполняемым
chmod +x deploy-dev.sh

# Запустите развертывание
./deploy-dev.sh
```

### 3. Развертывание для продакшна

```bash
# Создайте продакшн конфигурацию
cp .env.example .env.production
# Отредактируйте .env.production с продакшн значениями

# Сделайте скрипт исполняемым
chmod +x deploy-prod.sh

# Запустите продакшн развертывание
./deploy-prod.sh
```

## 📁 Структура контейнеров

### Backend API Container
- **Образ**: Python 3.11-slim
- **Порт**: 8000
- **Volumes**: logs, vectorstore, faiss_index
- **Health Check**: `/health` endpoint

### Telegram Bot Container
- **Образ**: Python 3.11-slim
- **Зависимости**: Backend API
- **Volumes**: logs

### Redis Container
- **Образ**: Redis 7-alpine
- **Порт**: 6379
- **Persistence**: Включена

## 🔧 Управление контейнерами

### Основные команды

```bash
# Запуск всех сервисов
docker-compose up -d

# Остановка всех сервисов
docker-compose down

# Просмотр логов
docker-compose logs -f

# Просмотр логов конкретного сервиса
docker-compose logs -f backend
docker-compose logs -f telegram_bot

# Перезапуск сервиса
docker-compose restart backend

# Проверка статуса
docker-compose ps

# Обновление образов
docker-compose pull
docker-compose up -d
```

### Продакшн команды

```bash
# Все команды с продакшн конфигурацией
docker-compose -f docker-compose.prod.yml [command]

# Например:
docker-compose -f docker-compose.prod.yml up -d
docker-compose -f docker-compose.prod.yml logs -f
```

## 🌐 Облачное развертывание

### Yandex Cloud (Container Registry)

1. **Подготовка образов для облака**:

```bash
# Тегирование образов
docker tag ai-bartender-backend cr.yandex/your-registry-id/ai-bartender-backend:latest
docker tag ai-bartender-bot cr.yandex/your-registry-id/ai-bartender-bot:latest

# Отправка в реестр
docker push cr.yandex/your-registry-id/ai-bartender-backend:latest
docker push cr.yandex/your-registry-id/ai-bartender-bot:latest
```

2. **Создание Container Instance**:

```bash
# Создание группы контейнеров
yc container container-group create \
  --name ai-bartender-group \
  --service-account-name your-service-account \
  --memory 2GB \
  --cores 1 \
  --container name=backend,image=cr.yandex/your-registry-id/ai-bartender-backend:latest,port=8000 \
  --container name=bot,image=cr.yandex/your-registry-id/ai-bartender-bot:latest
```

### Google Cloud Run

```bash
# Развертывание backend
gcloud run deploy ai-bartender-backend \
  --image gcr.io/your-project/ai-bartender-backend \
  --platform managed \
  --region us-central1 \
  --allow-unauthenticated \
  --memory 2Gi

# Развертывание bot
gcloud run deploy ai-bartender-bot \
  --image gcr.io/your-project/ai-bartender-bot \
  --platform managed \
  --region us-central1 \
  --memory 512Mi
```

### AWS ECS/Fargate

```bash
# Создание задачи ECS
aws ecs register-task-definition \
  --family ai-bartender-task \
  --network-mode awsvpc \
  --requires-compatibilities FARGATE \
  --cpu 1024 \
  --memory 2048 \
  --container-definitions file://ecs-task-definition.json
```

## 📊 Мониторинг и логирование

### Prometheus метрики
- Доступны по адресу: `http://localhost:9090`
- Метрики API: `http://localhost:8000/metrics`

### Логи
- Backend логи: `./logs/`
- Bot логи: `./logs/`
- Nginx логи: через volume `nginx_logs`

### Health Checks
- Backend: `GET /health`
- Bot: через зависимость от backend

## 🔒 Безопасность

### Переменные окружения
- Никогда не коммитьте `.env` файлы
- Используйте secrets в продакшне
- Регулярно ротируйте API ключи

### Сетевая безопасность
- Используйте внутренние сети Docker
- Ограничьте доступ к портам
- Настройте SSL/TLS для продакшна

## 🐛 Отладка

### Подключение к контейнеру
```bash
# Подключение к backend контейнеру
docker-compose exec backend bash

# Подключение к bot контейнеру
docker-compose exec telegram_bot bash
```

### Проверка переменных окружения
```bash
docker-compose exec backend env | grep -E "(YANDEX|TELEGRAM|S3)"
```

### Про��ерка сетевого подключения
```bash
# Из bot контейнера к backend
docker-compose exec telegram_bot curl http://backend:8000/health
```

## 📈 Масштабирование

### Горизонтальное масштабирование
```yaml
# В docker-compose.yml
services:
  backend:
    deploy:
      replicas: 3
    # ... остальная конфигурация
```

### Вертикальное масштабирование
```yaml
services:
  backend:
    deploy:
      resources:
        limits:
          memory: 4G
          cpus: "2.0"
```

## 🔄 CI/CD

### GitHub Actions пример
```yaml
name: Deploy AI Bartender
on:
  push:
    branches: [main]
jobs:
  deploy:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v2
      - name: Build and Deploy
        run: |
          docker-compose build
          # Развертывание на целевом сервере
```

## 📞 Поддержка

При возникновении проблем:
1. Проверьте логи: `docker-compose logs -f`
2. Проверьте статус: `docker-compose ps`
3. Проверьте health check: `curl http://localhost:8000/health`
4. Перезапустите сервисы: `docker-compose restart`
