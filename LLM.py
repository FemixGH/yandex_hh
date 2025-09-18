import json
import re
from logger import logger
from yandex_get_token import yandex_bot
from typing import List, Optional, Dict, Tuple

VACANSY: Dict[int, Dict[str, any]] = {}

SYSTEM_PROMPT = (
    "Ты - ассистент который должен помогать пользователям в создании их вакансий"
    "твоя задача выводить просто сообщение в формате json, без лишних слов"
    "если в вопросе нет запроса на создание вакансии, просто ответь что ты ассистент по созданию вакансий и ты не можешь отвечать ни на что иное"
    "title (строка), description (строка), skills (массив строк), "
    "min_experience_years (целое), salary_from (целое или null), salary_to (целое или null), "
    "location (строка или null), strictness (число от 0 до 1).\n\n"
    "Если поле не может быть извлечено — инициализируй его значением null (или пустым массивом для skills). "
    "Если поле не указано явно, то напиши о том какие поля не заполнены и требуется уточнение"
    "Если все извлечено, то просто верни json с заполненными полями, ничего больше"
    )

def call_llm(text: str) -> Tuple[bool, str]:
    prompt = SYSTEM_PROMPT + "\n\n" + text
    try:
        raw = yandex_bot.ask_gpt(prompt)
        logger.info(f"LLM responce: {raw}")
        return True, raw
    except Exception as e:
        logger.error(f"Pizdetz: {e}")
        return False, ""


def extract_json(text: str) -> Tuple[bool, dict]:
    try:
        start = str.lindex(text, '{')
        end = str.rindex(text, '}') + 1
        json_str = text[start:end]
        data = json.loads(json_str)
        return True, data
    except Exception as e:
        logger.error(f"JSON error parse: {e}")
        return False, {}

def merge_into_draft(user_id: int, extracted: dict) -> dict:
    """
    Merge non-null/non-empty fields from extracted into draft stored for user_id.
    Возвращает обновлённый draft.
    """
    draft = VACANSY.get(user_id, {})
    draft = dict(draft)  # shallow copy
    for k, v in extracted.items():
        if v is None:
            continue
        # normalize skills from string to list if needed
        if k == "skills" and isinstance(v, str):
            draft["skills"] = [s.strip() for s in v.split(",") if s.strip()]
        else:
            draft[k] = v
    VACANSY[user_id] = draft
    return draft