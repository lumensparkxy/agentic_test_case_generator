from typing import List, Optional

from pydantic import BaseModel, HttpUrl

from .test_cases import TestCase


class AutomationInput(BaseModel):
    test_cases: List[TestCase]
    target_base_url: Optional[HttpUrl] = None


class AutomationResponse(BaseModel):
    status: str
    files: List[str]
    notes: Optional[str] = None


__all__ = [
    "AutomationInput",
    "AutomationResponse",
]
