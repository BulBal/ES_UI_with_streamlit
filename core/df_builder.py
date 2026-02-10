import pandas as pd
from typing import Any, Dict, List


DEFAULT_COLUMNS = [
    "title", 
    "filename", 
    "extension", 
    "created_at", 
    "modified_at", 
    "path_real", 
    "path_virtual", 
    "path_tree", 
    "filesize_bytes"
    ]

def hits_to_rows(hits_raw: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        rows: List[Dict[str, Any]] = []

        for h in hits_raw:
            src = h.get("_source", {}) or {}

            # ✅ file/path만 있을 때 대비: None-safe
            row = {
                "doc_id": str(h.get("_id", "")),
                "score": float(h.get("_score", 0.0) or 0.0),

                # ---- 여기부터는 네 인덱스 필드명에 맞춰 고치면 됨 ----
                "filename": src.get("filename", "") or "",
                "extension": src.get("extension", "") or "",
                "created_at": src.get("created_at", "") or "",
                "modified_at": src.get("modified_at", "") or "",
                "path_real": src.get("path_real", "") or "",
                "path_virtual": src.get("path_virtual", "") or "",
                "path_tree": src.get("path_tree", "") or "",
                "filesize_bytes": int(src.get("filesize_bytes", 0) or 0),
            }

            rows.append(row)

        return rows
    
def rows_to_df(rows: List[Dict[str, Any]]) -> pd.DataFrame:
    """
    직렬화/표출 안정성을 위해:
    - index는 reset (0..N-1)
    - 컬럼 순서 고정
    """
    df = pd.DataFrame(rows)
    if df.empty:
        return df

    # ✅ 컬럼 순서: doc_id/score 먼저 + 나머지
    ordered = ["doc_id", "score"] + [c for c in DEFAULT_COLUMNS if c in df.columns]
    # 남는 컬럼이 있으면 뒤에 붙임
    rest = [c for c in df.columns if c not in ordered]
    df = df[ordered + rest].reset_index(drop=True)

    return df