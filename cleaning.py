TOXIC_PATTERNS = [
    r"\b(ненавижу|ненависть|убей|убийство|смерть|суицид|самоубийство)\b",
    r"\b(расизм|нацизм|ксенофобия|гомофобия|дискриминация)\b",
    r"\b(порно|порнография|секс|интим|изнасилование)\b",
    r"\b(наркотики|наркота|героин|кокаин|лсд|метамфетамин)\b",
    r"\b(оружие|бомба|терроризм|взрывчатка|стрельба)\b",
    r"\b(мошенничество|взлом|хакерство|фишинг|scam)\b",
]

COMPILED_TOXIC_PATTERNS = [re.compile(pattern, re.IGNORECASE | re.UNICODE) for pattern in TOXIC_PATTERNS]

def filter_toxic_content(text: str) -> str:
    """
    Фильтрует токсичный контент в выводе модели.
    Возвращает отфильтрованный текст или сообщение о блокировке.
    """
    for pattern in COMPILED_TOXIC_PATTERNS:
        if pattern.search(text):
            logger.warning(f"Обнаружен токсичный контент в выводе: {text[:100]}...")
            return "Извините, я не могу предоставить этот контент по соображениям безопасности."
    
    if len(text) > 4000:
        logger.warning("Ответ слишком длинный, обрезается")
        return text[:4000]

def check_answer_relevance(question: str, answer: str) -> bool:
    """
    Проверяет, соответствует ли ответ заданному вопросу.
    Возвращает True если ответ релевантен, False если нет.
    """
    if not answer.strip():
        return False
    
    if len(answer) < 10 and len(question) > 20:
        return False
    
    irrelevant_patterns = [
        r"^я не могу.*ответить",
        r"^извините.*не понимаю",
        r"^этот вопрос.*не по теме",
        r"^я не имею.*информации",
        r"^как ассистент.*не могу",
    ]
    
    for pattern in irrelevant_patterns:
        if re.search(pattern, answer, re.IGNORECASE):
            return False
    
    question_words = set(re.findall(r'\b\w{4,}\b', question.lower()))
    answer_words = set(re.findall(r'\b\w{4,}\b', answer.lower()))
    
    if question_words:
        common_words = question_words.intersection(answer_words)
        relevance_ratio = len(common_words) / len(question_words)
        
        if relevance_ratio < 0.2:
            logger.warning(f"Низкая релевантность ответа: {relevance_ratio:.2f}")
            return False
    
    return True