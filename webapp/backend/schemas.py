from __future__ import annotations

from typing import Optional

from pydantic import BaseModel


class AnalyzeResponse(BaseModel):
    session_id: int


class SessionStatus(BaseModel):
    id: int
    status: str
    result: Optional[dict] = None
    error: Optional[str] = None
    llm_cost_cny: Optional[float] = None
