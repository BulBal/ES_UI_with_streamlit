from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import date
from typing import Any, Dict, List, Optional

@dataclass
class SearchParams:
    q: str
    page: int
    size: int
    sort: str
    extension: Optional[str]
    created_from: Optional[date]
    created_to: Optional[date]
    modified_from: Optional[date]
    modified_to: Optional[date]
    selected_fields: Optional[List[str]]

class DslBuilder(ABC):
    """
    인덱스/스키마별 DSL 생성 전략 인터페이스.
    """
    @abstractmethod
    def build(self, p: SearchParams) -> Dict[str, Any]:
        raise NotImplementedError
