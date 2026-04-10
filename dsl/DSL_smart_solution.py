from typing import Any, Dict, List, Optional
from dsl.base import DslBuilder, SearchParams

FILENAME_TEXT_FIELDS = [
    ("filename", 6),
    ("filename.noun", 2),
    ("filename.en", 2),
    ("filename.en_edge", 1.2),
]

PATH_TEXT_FIELDS = [
    ("path_recent", 1.2),
    ("path_real.tree", 0.8),
]

EXACT_FIELDS = [
    ("filename.keyword", 20),
    
]

PHRASE_FIELDS = [
    ("filename", 12.0),

]

class DSLSmartSolutionDslBuilder(DslBuilder):
    def build(self, params: SearchParams) -> Dict[str, Any]:
        page = max(1, params.page)
        size = min(max(1, params.size), 3000)
        from_offset = (page - 1) * size
        q = (params.q or "").strip()

        should: List[Dict[str, Any]] = []
        filters: List[Dict[str, Any]] = []
        must_not: List[Dict[str, Any]] = []

        filename_fields = [f"{field}^{boost}" for field, boost in FILENAME_TEXT_FIELDS]
        path_fields = [f"{field}^{boost}" for field, boost in PATH_TEXT_FIELDS]

        # 1) exact
        for field, boost in EXACT_FIELDS:
            should.append({
                "term": {
                    field: {
                        "value": q,
                        "boost": boost
                    }
                }
            })

        # 2) phrase
        for field, boost in PHRASE_FIELDS:
            should.append({
                "match_phrase": {
                    field: {
                        "query": q,
                        "boost": boost
                    }
                }
            })

        # 3) main relevance (filename 중심)
        should.append({
            "multi_match": {
                "query": q,
                "type": "best_fields",
                "fields": filename_fields,
                "tie_breaker": 0.3,
                "operator": "or",
                "minimum_should_match": "1<-55% 4<-45%",
                "boost": 1.0
            }
        })

        # 4) path assist
        should.append({
            "multi_match": {
                "query": q,
                "type": "best_fields",
                "fields": path_fields,
                "tie_breaker": 0.2,
                "operator": "or",
                "boost": 0.7
            }
        })

        filters: List[Dict[str, Any]] = []
        if params.target_mode == "DIR_ONLY":
            filters.append({"term": {"extension": "__dir__"}})
        elif params.target_mode == "FILE_ONLY":
            must_not.append({"term": {"extension": "__dir__"}})

        # 6) extension
        if params.extension and params.target_mode != "DIR_ONLY":
            filters.append({"terms": {"extension": params.extension}})


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
            "must_not": must_not,
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