from typing import Any, Dict, List, Optional
from dsl.base import DslBuilder, SearchParams

FIELD_TO_ES = {
    "filename": [
        "filename",
        "filename.en"
    ],
    "path": [
        "path_recent",
        "path_real.tree"
    ],
}
DEFAULT_SEARCH_FIELDS = ["filename", "path"]

class DSLSmartSolutionDslBuilder(DslBuilder):
    def build(self, params: SearchParams) -> Dict[str, Any]:
        page = max(1, params.page)
        size = min(max(1, params.size), 50)
        from_offset = (page - 1) * size

        selected = params.selected_fields or DEFAULT_SEARCH_FIELDS
        q = (params.q or "").strip()

        filename_fields: List[str] = []
        path_fields: List[str] = []

        for k in selected:
            if k == "filename":
                filename_fields.extend(FIELD_TO_ES.get("filename", []))
            elif k == "path":
                path_fields.extend(FIELD_TO_ES.get("path", []))

        if not filename_fields and not path_fields:
            filename_fields = FIELD_TO_ES["filename"]

        should: List[Dict[str, Any]] = []

        # 1) exact
        if "filename" in selected or not selected:
            should.append({
                "term": {
                    "filename.keyword": {
                        "value": q,
                        "boost": 15
                    }
                }
            })

            should.append({
                "match_phrase": {
                    "filename": {
                        "query": q,
                        "boost": 8
                    }
                }
            })

        # 2) main relevance (filename 중심)
        if filename_fields:
            should.append({
                "multi_match": {
                    "query": q,
                    "type": "cross_fields",
                    "fields": filename_fields,
                    "operator": "or",
                    "minimum_should_match": "2<-35% 6<-40%",
                    "boost": 1.0
                }
            })

        # 3) path assist
        if path_fields:
            should.append({
                "multi_match": {
                    "query": q,
                    "type": "best_fields",
                    "fields": path_fields,
                    "tie_breaker": 0.2,
                    "minimum_should_match": "60%",
                    "boost": 0.7
                }
            })

        filters: List[Dict[str, Any]] = []

        if params.extension:
            filters.append({"term": {"extension": params.extension.lower()}})

        if params.created_from or params.created_to:
            created_range: Dict[str, Any] = {}
            if params.created_from:
                created_range["gte"] = params.created_from.isoformat()
            if params.created_to:
                created_range["lte"] = params.created_to.isoformat()
            filters.append({"range": {"created_at": created_range}})

        if params.modified_from or params.modified_to:
            modified_range: Dict[str, Any] = {}
            if params.modified_from:
                modified_range["gte"] = params.modified_from.isoformat()
            if params.modified_to:
                modified_range["lte"] = params.modified_to.isoformat()
            filters.append({"range": {"modified_at": modified_range}})

        bool_q: Dict[str, Any] = {
            "should": should,
            "minimum_should_match": 1
        }
        if filters:
            bool_q["filter"] = filters

        dsl: Dict[str, Any] = {
            "track_total_hits": True,
            "from": from_offset,
            "size": size,
            "_source": [
                "filename", "path_virtual", "path_real",
                "extension", "created_at", "modified_at",
                "filesize"
            ],
            "query": {
                "bool": bool_q
            },
            "highlight": {
                "pre_tags": ["<mark>"],
                "post_tags": ["</mark>"],
                "require_field_match": False,
                "fields": {
                    "filename": {"number_of_fragments": 0},
                    "filename.noun": {"number_of_fragments": 0}
                }
            },
            "sort": (
                [{"modified_at": {"order": "desc"}}]
                if params.sort == "RECENCY"
                else [{"_score": {"order": "desc"}}]
            )
        }

        return dsl