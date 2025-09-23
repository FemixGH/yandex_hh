#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Утилита для загрузки секретов из Yandex Lockbox в переменные окружения.

Использование:
    from lockbox_loader import load_lockbox_env
    load_lockbox_env()  # возьмёт SECRET_ID из окружения

Поведение:
- Если SECRET_ID не задан, тихо ничего не делает (удобно для локальной разработки).
- Получает IAM токен из метаданных Serverless/VM; если недоступно, пытается через yandex_jwt_auth.get_iam_token().
- Запрашивает payload у Lockbox API и записывает пары key -> textValue в os.environ.
- По умолчанию не перезаписывает уже заданные переменные окружения (override=False).
"""

import os
import json
import urllib.request
import urllib.error
from typing import Optional, Dict, Any

# Пытаемся использовать общий JWT->IAM модуль как резерв
try:
    from yandex_jwt_auth import get_iam_token as _jwt_to_iam
except Exception:  # pragma: no cover
    _jwt_to_iam = None


def _get_token_from_metadata(timeout: int = 3) -> Optional[str]:
    """Пробует получить IAM токен из метаданных (Serverless/VM)."""
    urls = [
        "http://169.254.169.254/computeMetadata/v1/instance/service-accounts/default/token",
        "http://169.254.169.254/computeMetadata/v1/instance/service-accounts/default/identity",
    ]
    for url in urls:
        try:
            req = urllib.request.Request(url)
            req.add_header("Metadata-Flavor", "Google")
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.load(resp)
                token = data.get("access_token") or data.get("token")
                if token:
                    return token
        except Exception:
            continue
    return None


def _get_iam_token() -> str:
    """Возвращает IAM токен для обращения к Lockbox API."""
    env_token = os.environ.get("IAM_TOKEN")
    if env_token:
        return env_token

    token = _get_token_from_metadata()
    if token:
        return token

    if _jwt_to_iam is not None:
        return _jwt_to_iam()

    raise RuntimeError(
        "IAM токен не найден. Установите IAM_TOKEN, назначьте сервисный аккаунт контейнеру или настройте JWT-обмен."
    )


def _fetch_lockbox_payload(secret_id: str, version_id: Optional[str] = None, iam_token: Optional[str] = None) -> Dict[str, Any]:
    """Запрашивает payload секрета у Lockbox API."""
    if not iam_token:
        iam_token = _get_iam_token()

    url = f"https://payload.lockbox.api.cloud.yandex.net/lockbox/v1/secrets/{secret_id}/payload"
    if version_id:
        url += f"?versionId={version_id}"

    headers = {"Authorization": f"Bearer {iam_token}", "Content-Type": "application/json"}
    req = urllib.request.Request(url, headers=headers)
    with urllib.request.urlopen(req, timeout=10) as resp:
        return json.load(resp)


def _parse_entries(payload: Dict[str, Any]) -> Dict[str, str]:
    """Парсит entries -> словарь key -> text_value."""
    result: Dict[str, str] = {}
    for e in payload.get("entries", []):
        key = e.get("key") or e.get("Key")
        text_value = e.get("textValue") if e.get("textValue") is not None else e.get("text_value")
        if key and text_value is not None:
            result[key] = str(text_value)
    return result


def load_lockbox_env(secret_id: Optional[str] = None, version_id: Optional[str] = None, override: bool = False) -> Dict[str, str]:
    """Загружает секрет из Lockbox и переносит ключи в os.environ.

    :param secret_id: ID секрета (если None, берётся из SECRET_ID)
    :param version_id: ID версии (по умолчанию текущая)
    :param override: Перезаписывать существующие переменные окружения
    :return: Словарь установленных пар ключ-значение
    """
    sid = secret_id or os.environ.get("SECRET_ID")
    if not sid:
        return {}

    payload = _fetch_lockbox_payload(sid, version_id)
    kv = _parse_entries(payload)

    applied: Dict[str, str] = {}
    for k, v in kv.items():
        if override or (k not in os.environ or os.environ.get(k) in (None, "")):
            os.environ[k] = v
            applied[k] = v
    return applied

