from pydantic import BaseModel, Field, field_validator, ConfigDict
from typing import List, Optional, Annotated
from pydantic.types import StringConstraints

# Создаем тип с ограничениями для строк
ConstrainedStr = Annotated[str, StringConstraints(strip_whitespace=True)]
ShortStr = Annotated[ConstrainedStr, StringConstraints(min_length=1, max_length=60)]
TitleStr = Annotated[ConstrainedStr, StringConstraints(min_length=3, max_length=120)]
LongText = Annotated[ConstrainedStr, StringConstraints(max_length=5000)]
LocationStr = Annotated[ConstrainedStr, StringConstraints(max_length=120)]
EducationStr = Annotated[ConstrainedStr, StringConstraints(max_length=500)]
AchievementsStr = Annotated[ConstrainedStr, StringConstraints(max_length=2000)]
EmailStr = Annotated[ConstrainedStr, StringConstraints(max_length=254)]
PhoneStr = Annotated[ConstrainedStr, StringConstraints(max_length=40)]

class VacancyInput(BaseModel):
    model_config = ConfigDict(use_enum_values=True)
    
    title: TitleStr
    description: Optional[LongText] = None
    skills: Optional[List[ShortStr]] = Field(default_factory=list)
    min_experience_years: Optional[int] = Field(default=0, ge=0, le=50)
    salary_from: Optional[int] = Field(default=None, ge=0)
    salary_to: Optional[int] = Field(default=None, ge=0)
    location: Optional[LocationStr] = "Удалённо"
    strictness: Optional[float] = Field(default=0.5, ge=0.0, le=1.0)

    @field_validator("skills", mode="before")
    @classmethod
    def normalize_skills(cls, v):
        if not v:
            return []
        # allow comma separated string or list
        if isinstance(v, str):
            skills = [s.strip() for s in v.split(",") if s.strip()]
            return skills
        return v

    @field_validator("salary_to")
    @classmethod
    def salary_range_ok(cls, v, info):
        values = info.data
        if v is not None and values.get("salary_from") is not None:
            if v < values["salary_from"]:
                raise ValueError("salary_to не может быть меньше salary_from")
        return v

class ResumeInput(BaseModel):
    model_config = ConfigDict(use_enum_values=True)
    
    full_name: Annotated[ConstrainedStr, StringConstraints(min_length=2, max_length=120)]
    title: Optional[TitleStr] = None
    experience_years: Optional[int] = Field(default=0, ge=0, le=60)
    skills: Optional[List[ShortStr]] = Field(default_factory=list)
    education: Optional[EducationStr] = None
    achievements: Optional[AchievementsStr] = None
    contact_email: Optional[EmailStr] = None
    contact_phone: Optional[PhoneStr] = None

    @field_validator("skills", mode="before")
    @classmethod
    def normalize_skills(cls, v):
        if not v:
            return []
        if isinstance(v, str):
            return [s.strip() for s in v.split(",") if s.strip()]
        return v