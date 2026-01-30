import json
from typing import Any, Dict, List, Tuple

import requests
from requests.auth import HTTPBasicAuth

from core.config import AppConfig
from core.models import EsHit

class EsClient:
    def __init__(self, cfg: AppConfig):
        self.cfg = cfg

    def search(self, index: str, dsl: Dict[str, Any]) -> Tuple[int, List[EsHit]]:
        url = f"{self.cfg.es_base_url.rstrip('/')}/{index}/_search"
        r = requests.post(
            url,
            auth=HTTPBasicAuth(self.cfg.es_user, self.cfg.es_pass),
            headers={"Content-Type": "application/json"},
            data=json.dumps(dsl),
            verify=self.cfg.es_verify_ssl,
            timeout=15,
        )
        r.raise_for_status()
        payload = r.json()

        total = int(payload.get("hits", {}).get("total", {}).get("value", 0))
        hits_raw = payload.get("hits", {}).get("hits", [])

        hits: List[EsHit] = []
        for h in hits_raw:
            src = h.get("_source", {}) or {}
            hl = h.get("highlight", {}) or {}
            hits.append(EsHit(
                id=str(h.get("_id", "")),
                score=float(h.get("_score", 0.0) or 0.0),
                title=str(src.get("title", "") or ""),
                filename=str(src.get("filename", "") or ""),
                path_virtual=str(src.get("path_virtual", "") or ""),
                path_real=str(src.get("path_real", "") or ""),
                extension=str(src.get("extension", "") or ""),
                created_at=str(src.get("created_at", "") or ""),
                modified_at=str(src.get("modified_at", "") or ""),
                filesize_bytes=int(src.get("filesize_bytes", 0) or 0),
                highlights={k: [str(x) for x in v] for k, v in hl.items()},
            ))
        return total, hits

    def list_indices(self) -> List[str]:
        url = f"{self.cfg.es_base_url.rstrip('/')}/_cat/indices"
        r = requests.get(
            url,
            auth=HTTPBasicAuth(self.cfg.es_user, self.cfg.es_pass),
            params={"format": "json", "h": "index"},
            verify=self.cfg.es_verify_ssl,
            timeout=10,
        )
        r.raise_for_status()
        rows = r.json() or []
        return sorted({row.get("index") for row in rows if row.get("index")})
