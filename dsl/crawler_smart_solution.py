from typing import Any, Dict, List, Optional
from dsl.base import DslBuilder, SearchParams

FIELD_TO_ES = {
    "filename": [
        "filename^8",
        "filename.num_unit^10",
        "filename.edge^2",
        "filename.en^3"
    ],
    "keywords": [
        "keywords^5"
    ],
    "path": [
        "path_real.tree^3"
    ],
}
DEFAULT_SEARCH_FIELDS = ["filename", "path"]

class CrawlerSmartSolutionDslBuilder(DslBuilder):
    def build(self, params: SearchParams) -> Dict[str, Any]:
        page = max(1, params.page)
        size = min(max(1, params.size), 50)
        from_offset = (page - 1) * size

        selected = params.selected_fields or DEFAULT_SEARCH_FIELDS

        fields: List[str] = []
        for k in selected:
            fields.extend(FIELD_TO_ES.get(k, []))
        if not fields:
            fields = FIELD_TO_ES["filename"]
        should = [
            {"term": {"filename.keyword": {"value": params.q, "boost": 20}}},
        ]

        # 여기서 검색어에 대한 검색 설정을 더 세밀하게 조정할 수 있음 (예: 특정 필드 가중치, 매칭 유형, 분석기 명시 등)
        must = [{
            "multi_match": {
                "query": params.q,
                "fields": fields,
                "type": "most_fields",
                "operator": "or",
                "minimum_should_match": "70%"
            }
        }]

        filters: List[Dict[str, Any]] = []
        if params.extension:
            filters.append({"term": {"extension": params.extension.lower()}})

        if params.created_from or params.created_to:
            modified_range: Dict[str, Any] = {}
            create_range: Dict[str, Any] = {}
            if params.created_from: create_range["gte"] = params.created_from.isoformat()
            if params.created_to:   create_range["lte"] = params.created_to.isoformat()
            filters.append({"range": {"created_at": create_range}})

        if params.modified_from or params.modified_to:
            modified_range: Dict[str, Any] = {}
            if params.modified_from: modified_range["gte"] = params.modified_from.isoformat()
            if params.modified_to:   modified_range["lte"] = params.modified_to.isoformat()
            filters.append({"range": {"modified_at": modified_range}})

        bool_q: Dict[str, Any] = {"must": must, "should": should, "minimum_should_match": 0}
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
            "query": {"bool": bool_q},
            "highlight": {
                "pre_tags": ["<mark>"],
                "post_tags": ["</mark>"],
                "require_field_match": False,
                "fields": {
                    "filename": {"number_of_fragments": 0},
                }
            },
            "sort": [{"modified_at": {"order": "desc"}}] if params.sort == "RECENCY" else [{"_score": {"order": "desc"}}],
        }
        return dsl
