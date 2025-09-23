import re
from typing import List

# --- функция экранирования для MarkdownV2 (используй ту же, что у тебя, если есть) ---
def escape_markdown_v2(text: str) -> str:
    if text is None:
        return ""
    escape_chars = r'_*[]()~`>#+-=|{}.!'
    # Экранируем каждый "спец" символ префиксом '\'
    out = []
    for ch in text:
        if ch in escape_chars:
            out.append('\\' + ch)
        else:
            out.append(ch)
    return "".join(out)

# --- форматтер конкретно под твой шаблон ---
def format_cocktail_markdown(raw: str) -> str:
    """
    Принимает строку (ответ модели) и возвращает MarkdownV2-совместимый текст.
    Поддерживаются заголовки: Коктейль:, ИНГРЕДИЕНТЫ:, ПРИГОТОВЛЕНИЕ:, ИНТЕРЕСНЫЙ ФАКТ:
    """
    if not raw:
        return ""

    lines = [ln.rstrip() for ln in raw.splitlines()]
    out_lines: List[str] = []
    i = 0
    n = len(lines)

    # helper: взять следующие непустые строки блока
    def collect_block(start_idx):
        j = start_idx
        block = []
        while j < n and lines[j].strip() != "":
            block.append(lines[j])
            j += 1
        return block, j

    while i < n:
        line = lines[i].strip()
        if not line:
            # пустая строка -> даём перенос
            out_lines.append("")
            i += 1
            continue

        low = line.lower()
        # Коктейль: <NAME>
        if low.startswith("коктейль:") or low.startswith("cocktail:"):
            parts = line.split(":", 1)
            name = parts[1].strip() if len(parts) > 1 else ""
            name_esc = escape_markdown_v2(name)
            # делаем жирный заголовок и курсивное имя в кавычках
            out_lines.append(f"*Коктейль:* _\"{name_esc}\"_")
            i += 1
            continue

        # ИНГРЕДИЕНТЫ:
        if line.upper().startswith("ИНГРЕДИЕНТ") or line.upper().startswith("INGREDIENT"):
            out_lines.append("*ИНГРЕДИЕНТЫ:*")
            block, j = collect_block(i + 1)
            for l in block:
                # допустимо, что строка начинается с '- ' или '• '
                ing = re.sub(r'^\s*[-\u2022]\s*', '', l).strip()
                out_lines.append(f"• {escape_markdown_v2(ing)}")
            i = j
            continue

        # ПРИГОТОВЛЕНИЕ:
        if line.upper().startswith("ПРИГОТОВЛЕНИЕ") or line.upper().startswith("PREPARATION"):
            out_lines.append("*ПРИГОТОВЛЕНИЕ:*")
            block, j = collect_block(i + 1)
            # если шаги нумерованы или просто перечисление — нормализуем в 1.,2.,..
            step = 1
            for l in block:
                s = re.sub(r'^\s*[-\u2022]?\s*\d*\.*\s*', '', l).strip()
                out_lines.append(f"{step}. {escape_markdown_v2(s)}")
                step += 1
            i = j
            continue

        # ИНТЕРЕСНЫЙ ФАКТ:
        if line.upper().startswith("ИНТЕРЕСНЫЙ") or line.upper().startswith("INTERESTING FACT"):
            # можем брать либо в той же строке после ':' либо следующий непустой
            if ":" in line:
                fact = line.split(":", 1)[1].strip()
                out_lines.append(f"_{escape_markdown_v2(fact)}_")
                i += 1
            else:
                block, j = collect_block(i + 1)
                if block:
                    out_lines.append(f"_{escape_markdown_v2(' '.join(block))}_")
                i = j
            continue

        # Остальное — просто экранируем и вставляем как текст
        out_lines.append(escape_markdown_v2(line))
        i += 1

    # Сжимаем подряд идущие пустые строки (максимум одна)
    final_lines = []
    prev_empty = False
    for ln in out_lines:
        if ln == "":
            if not prev_empty:
                final_lines.append(ln)
            prev_empty = True
        else:
            final_lines.append(ln)
            prev_empty = False

    return "\n".join(final_lines)
