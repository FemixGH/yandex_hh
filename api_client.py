#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
API клиент для взаимодействия с FastAPI бэкендом ИИ Бармена
"""

import requests
import json
from typing import Dict, List, Optional, Any
from datetime import datetime
import logging

logger = logging.getLogger(__name__)

class BartenderAPIClient:
    """Клиент для взаимодействия с API ИИ Бармена"""

    def __init__(self, base_url: str = "http://localhost:8000", timeout: int = 30):
        self.base_url = base_url.rstrip('/')
        self.timeout = timeout
        self.session = requests.Session()

    def _make_request(self, method: str, endpoint: str, **kwargs) -> Dict[str, Any]:
        """Выполняет HTTP запрос к API"""
        url = f"{self.base_url}{endpoint}"

        try:
            response = self.session.request(
                method=method,
                url=url,
                timeout=self.timeout,
                **kwargs
            )
            response.raise_for_status()
            return response.json()

        except requests.exceptions.RequestException as e:
            logger.error(f"Ошибка запроса к {url}: {e}")
            raise

    def health_check(self) -> Dict[str, Any]:
        """Проверка здоровья API"""
        return self._make_request("GET", "/health")

    def ask_bartender(self, query: str, user_id: Optional[str] = None,
                     k: int = 3, with_moderation: bool = True) -> Dict[str, Any]:
        """Отправка запроса к барменскому ИИ"""
        data = {
            "query": query,
            "k": k,
            "with_moderation": with_moderation
        }
        if user_id:
            data["user_id"] = user_id

        return self._make_request("POST", "/bartender/ask", json=data)

    def check_moderation(self, text: str, is_input: bool = True) -> Dict[str, Any]:
        """Проверка модерации текста"""
        data = {
            "text": text,
            "is_input": is_input
        }
        return self._make_request("POST", "/moderation/check", json=data)

    def create_embedding(self, text: str, model_uri: Optional[str] = None) -> Dict[str, Any]:
        """Создание эмбеддинга для текста"""
        data = {"text": text}
        if model_uri:
            data["model_uri"] = model_uri

        return self._make_request("POST", "/embeddings/create", json=data)

    def semantic_search(self, query: str, k: int = 5, threshold: float = 0.5) -> Dict[str, Any]:
        """Семантический поиск в векторной базе"""
        data = {
            "query": query,
            "k": k,
            "threshold": threshold
        }
        return self._make_request("POST", "/search/semantic", json=data)

    def get_index_status(self) -> Dict[str, Any]:
        """Получение статуса векторного индекса"""
        return self._make_request("GET", "/index/status")

    def rebuild_index(self, bucket: str, prefix: str = "", force: bool = False) -> Dict[str, Any]:
        """Перестройка векторного индекса"""
        data = {
            "bucket": bucket,
            "prefix": prefix,
            "force": force
        }
        return self._make_request("POST", "/index/rebuild", json=data)

    def update_index(self) -> Dict[str, Any]:
        """Инкрементальное обновление индекса"""
        return self._make_request("POST", "/index/update")

    def generate_completion(self, prompt: str, model_uri: Optional[str] = None,
                          max_tokens: int = 2000, temperature: float = 0.3) -> Dict[str, Any]:
        """Генерация текста через Yandex GPT"""
        params = {
            "prompt": prompt,
            "max_tokens": max_tokens,
            "temperature": temperature
        }
        if model_uri:
            params["model_uri"] = model_uri

        return self._make_request("POST", "/completion/generate", params=params)


def test_api_endpoints():
    """Тестирование основных эндпоинтов API"""
    client = BartenderAPIClient()

    print("🔍 Тестирование API ИИ Бармена")
    print("=" * 50)

    try:
        # Проверка здоровья
        print("1. Проверка здоровья сервиса...")
        health = client.health_check()
        print(f"   ✅ Статус: {health.get('status')}")
        print(f"   📊 Документов в индексе: {health.get('vectorstore', {}).get('documents', 0)}")

        # Статус индекса
        print("\n2. Статус векторного индекса...")
        index_status = client.get_index_status()
        print(f"   📁 Существует: {index_status.get('exists')}")
        print(f"   📄 Документов: {index_status.get('documents_count')}")

        # Тест модерации
        print("\n3. Тест модерации...")
        moderation = client.check_moderation("Рецепт мохито", is_input=True)
        print(f"   🛡️ Безопасно: {moderation.get('is_safe')}")

        # Тест эмбеддинга
        print("\n4. Тест создания эмбеддинга...")
        embedding = client.create_embedding("коктейль мохито")
        print(f"   🔢 Размерность: {embedding.get('dimension')}")

        # Тест поиска
        print("\n5. Тест семантического поиска...")
        search = client.semantic_search("рецепт мохито", k=3)
        print(f"   🔍 Найдено результатов: {search.get('total_found')}")

        # Тест барменского ИИ
        print("\n6. Тест барменского ИИ...")
        response = client.ask_bartender("Как приготовить мохито?", user_id="test_user")
        print(f"   🤖 Ответ получен: {len(response.get('answer', ''))} символов")
        print(f"   ⏱️ Время обработки: {response.get('processing_time', 0):.2f}s")
        print(f"   🚫 Заблокирован: {response.get('blocked', False)}")

        print("\n✅ Все тесты пройдены успешно!")

    except Exception as e:
        print(f"\n❌ Ошибка при тестировании: {e}")
        return False

    return True


if __name__ == "__main__":
    # Простой тест API
    test_api_endpoints()
