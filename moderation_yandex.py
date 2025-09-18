# moderation_yandex.py
import logging
import re
from typing import Tuple
from yandex_api import yandex_classify, yandex_completion

logger = logging.getLogger(__name__)

# базовые паттерны (ваши уже были) — быстрый фильтр
TOXIC_PATTERNS = [
    r"\b(убий|убей|самоубийств|суицид)\b",
    r"\b(порно|порнограф|изнасилован)\b",
    r"\b(наркотик|героин|кокаин|лсд|метамфетам)\b",
    r"\b(террор|бомб|взорв)\b",
]
COMPILED = [re.compile(p, re.IGNORECASE | re.UNICODE) for p in TOXIC_PATTERNS]

def quick_check(text: str) -> Tuple[bool, dict]:
    for pat in COMPILED:
        if pat.search(text):
            logger.warning("quick_check blocked pattern %s", pat.pattern)
            return False, {"reason": "pattern", "pattern": pat.pattern}
    return True, {}

def llm_check_with_yandex(text: str) -> Tuple[bool, dict]:
    """
    Попытка использовать TextClassification или completion с подсказкой-модератором.
    Сначала попробуем TextClassification (если есть модель), иначе fallback на completion prompt.
    """
    # Попытка через classify (если настроен modelUri)
    resp = yandex_classify(text)
    if resp and not resp.get("error"):
        # структура ответа зависит от модели/настройки.
        # Ожидаем, что модель вернёт поле 'predictions' или 'classes' — подстройте при тестировании
        return True, {"via": "classify", "raw": resp}
    # fallback: попросим модель кратко пометить текст как SAFE / UNSAFE
    prompt = [
        {"role": "system", "text": "Ты — модератор. Ответь одним словом: SAFE или UNSAFE. Кратко, без пояснений."},
        {"role": "user", "text": f"Проверь текст на опасность: \"{text}\". Дай метку SAFE или UNSAFE."}
    ]
    cresp = yandex_completion(prompt)
    if cresp.get("error"):
        logger.error("yandex completion для модерации вернул ошибку")
        return True, {"via": "error_fallback", "raw": cresp}
    # в ответе ищем alternatives[0].message.text
    try:
        alt = cresp.get("alternatives", [])[0]
        msg = alt.get("message", {}).get("text", "")
        decision = "SAFE" if "SAFE" in msg.upper() else "UNSAFE"
        return decision == "SAFE", {"via": "completion", "label": decision, "raw_text": msg}
    except Exception as e:
        logger.exception("Ошибка парсинга результата модерации: %s", e)
        return True, {"via": "parse_error", "error": str(e)}

def pre_moderate_input(text: str) -> Tuple[bool, dict]:
    ok, meta = quick_check(text)
    if not ok:
        return False, meta
    return llm_check_with_yandex(text)

def post_moderate_output(text: str) -> Tuple[bool, dict]:
    # проверка по паттернам
    for pat in COMPILED:
        if pat.search(text):
            return False, {"reason": "post_pattern", "pattern": pat.pattern}
    return llm_check_with_yandex(text)
