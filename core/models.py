from dataclasses import dataclass
from typing import Dict, List

@dataclass
class EsHit:
    id: str
    score: float
    filename: str
    path_virtual: str
    path_real: str
    extension: str
    created_at: str
    modified_at: str
    filesize_bytes: int
    highlights: Dict[str, List[str]]
