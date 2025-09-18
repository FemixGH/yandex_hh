from pydantic import ValidationError
from model import DraftModel, TitleStr, ShortStr, EducationStr, AchievementsStr, VacancyInput

def validate_draft(draft: dict) -> tuple[bool, list]:
    try:
        VacancyInput(**draft)
        return True, []
    except ValidationError as e:
        errors = [err['msg'] for err in e.errors()]
        return False, errors
    
def fields_missing_message(errors: list) -> str:
    if not errors:
        return ""
    lines = ["Пожалуйста, уточните следующие поля:"]
    for err in errors:
        lines.append(f"- {err}")
    return "\n".join(lines)


        
        
        
