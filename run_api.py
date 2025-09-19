#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Скрипт для запуска FastAPI сервера в режиме разработки
"""

import os
import sys
import uvicorn
from pathlib import Path

# Добавляем текущую директорию в PYTHONPATH
current_dir = Path(__file__).parent
sys.path.insert(0, str(current_dir))

def main():
    """Запуск FastAPI сервера"""
    # Параметры запуска
    host = os.getenv("API_HOST", "127.0.0.1")
    port = int(os.getenv("API_PORT", "8000"))
    reload = os.getenv("API_RELOAD", "true").lower() == "true"
    log_level = os.getenv("API_LOG_LEVEL", "info")

    print(f"🚀 Запуск ИИ Бармен API на http://{host}:{port}")
    print(f"📚 Документация доступна на http://{host}:{port}/docs")
    print(f"🔄 Автоперезагрузка: {'включена' if reload else 'отключена'}")

    uvicorn.run(
        "backend_api:app",
        host=host,
        port=port,
        reload=reload,
        log_level=log_level,
        access_log=True
    )

if __name__ == "__main__":
    main()
