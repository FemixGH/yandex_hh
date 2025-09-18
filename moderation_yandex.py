# moderation_yandex.py
import logging
import re
from typing import Tuple
from yandex_api import yandex_completion
import json

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

def llm_moderation_yandex(text: str) -> Tuple[bool, dict]:
    """
    Надёжная версия: всегда возвращает (ok: bool, meta: dict).
    Использует LLM (yandex_completion) и гарантирует fallback в случае ошибок.
    """
    try:
        # quick pattern check already handled outside; here only LLM check
        prompt = [
            {"role": "system", "text": (
                "Ты модератор. Определи, опасно ли содержимое текста. "
                "Если это обычные запросы про еду, напитки, рецепты, развлечения, пожелания перчинки или более острое блюдо, если приходит про сообщение коктейль | безалкогольный и тд. — пометь как SAFE. "
                "Если текст про наркотики, насилие, самоповреждение, незаконную деятельность — UNSAFE. "
                "Верни ровно одно слово: SAFE или UNSAFE."
            )},
            {"role": "user", "text": f"Проверить текст: \"{text}\". Только одно слово: SAFE или UNSAFE."}
        ]
        cresp = yandex_completion(prompt)
        if cresp.get("error"):
            logger.warning("llm_moderation_yandex: completion returned error: %s", cresp)
            return True, {"via": "completion_error", "raw": cresp}

        # log raw response for debugging
        logger.debug("llm_moderation raw json: %s", json.dumps(cresp, ensure_ascii=False))

        txt = extract_text_from_yandex_completion(cresp)
        # If model returned nothing sensible, treat as SAFE by default (for user queries)
        if not txt or txt.strip() == "" or txt.strip().lower() == "assistant":
            logger.info("llm_moderation_yandex: model returned empty text; treating as SAFE")
            return True, {"via": "completion", "label": "SAFE", "raw_text": txt}

        label = "SAFE" if "SAFE" in txt.upper() else "UNSAFE"
        return (label == "SAFE"), {"via": "completion", "label": label, "raw_text": txt}
    except Exception as e:
        logger.exception("llm_moderation_yandex exception: %s", e)
        # безопасный fallback — считать текст безопасным, но пометить в метаданных
        return True, {"via": "exception", "error": str(e)}

def pre_moderate_input(text: str) -> Tuple[bool, dict]:
    """
    Надёжно вызывает quick_check + llm_moderation_yandex и ВЕРНЁТ кортеж в любом случае.
    """
    try:
        ok, meta = quick_check(text)
        if not ok:
            return False, meta  # quick_check уже возвращает объяснение
        res = llm_moderation_yandex(text)
        # защита на случай, если llm_moderation_yandex вдруг вернёт None или не кортеж
        if not isinstance(res, tuple) or len(res) != 2:
            logger.warning("pre_moderate_input: llm_moderation_yandex returned unexpected value: %r", res)
            return True, {"via": "fallback", "reason": "llm_moderation_bad_return"}
        return res
    except Exception as e:
        logger.exception("pre_moderate_input exception: %s", e)
        # по безопасности — пусть запрос пройдёт модерацию (можно поменять поведение)
        return True, {"via": "exception", "error": str(e)}

def post_moderate_output(text: str) -> Tuple[bool, dict]:
    # quick pattern check first
    for pat in COMPILED:
        if pat.search(text):
            return False, {"reason": "post_pattern", "pattern": pat.pattern}
    return llm_moderation_yandex(text)

def extract_text_from_yandex_completion(resp_json: dict) -> str:
    """
    Robust extraction of textual content from Yandex completion response.
    Returns empty string if no sensible text was found.
    """
    if not isinstance(resp_json, dict):
        return ""

    # Try canonical "result" -> "choices" -> message/content/text
    try:
        res = resp_json.get("result")
        if isinstance(res, dict):
            choices = res.get("choices")
            if isinstance(choices, list) and len(choices) > 0:
                ch = choices[0]
                # If choice directly contains content list
                if isinstance(ch, dict):
                    # content (list of dicts)
                    if "content" in ch and isinstance(ch["content"], list):
                        texts = []
                        for c in ch["content"]:
                            if isinstance(c, dict) and "text" in c and isinstance(c["text"], str):
                                texts.append(c["text"])
                        if texts:
                            return "\n".join(texts).strip()
                    # message object
                    if "message" in ch and isinstance(ch["message"], dict):
                        msg = ch["message"]
                        if "content" in msg and isinstance(msg["content"], list):
                            texts = []
                            for c in msg["content"]:
                                if isinstance(c, dict) and "text" in c and isinstance(c["text"], str):
                                    texts.append(c["text"])
                            if texts:
                                return "\n".join(texts).strip()
                        if "text" in msg and isinstance(msg["text"], str):
                            # ensure it's not just 'assistant' role leaked
                            if msg["text"].strip().lower() != "assistant":
                                return msg["text"].strip()
    except Exception:
        # fallthrough to generic search
        pass

    # Old-style alternatives handling
    if "alternatives" in resp_json and isinstance(resp_json["alternatives"], list) and resp_json["alternatives"]:
        alt = resp_json["alternatives"][0]
        if isinstance(alt, dict):
            msg = alt.get("message") or alt.get("content") or alt.get("text")
            if isinstance(msg, dict) and "text" in msg and isinstance(msg["text"], str):
                if msg["text"].strip().lower() != "assistant":
                    return msg["text"].strip()
            if isinstance(msg, str):
                if msg.strip().lower() != "assistant":
                    return msg.strip()
            if isinstance(alt.get("message"), dict) and isinstance(alt["message"].get("content"), list):
                texts = []
                for c in alt["message"]["content"]:
                    if isinstance(c, dict) and "text" in c and isinstance(c["text"], str):
                        texts.append(c["text"])
                if texts:
                    return "\n".join(texts).strip()

    # Generic search: find first non-role string (skip common role labels)
    def find_first_nonrole_string(obj):
        if isinstance(obj, str):
            s = obj.strip()
            if s and s.lower() not in ("assistant", "user", "system"):
                return s
            return None
        if isinstance(obj, dict):
            for k, v in obj.items():
                if k == "role":
                    continue
                found = find_first_nonrole_string(v)
                if found:
                    return found
        if isinstance(obj, list):
            for v in obj:
                found = find_first_nonrole_string(v)
                if found:
                    return found
        return None

    s = find_first_nonrole_string(resp_json)
    return s[:4000].strip() if s else ""