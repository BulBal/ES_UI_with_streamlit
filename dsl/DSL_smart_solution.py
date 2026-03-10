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

        fields: List[str] = []
        for k in selected:
            fields.extend(FIELD_TO_ES.get(k, []))
        if not fields:
            fields = FIELD_TO_ES["filename"]
        q = params.q

        # 여기서 검색어에 대한 검색 설정을 더 세밀하게 조정할 수 있음 (예: 특정 필드 가중치, 매칭 유형, 분석기 명시 등)
        should: List[Dict[str, Any]] = [

            # 1️⃣ exact match
            {
                "term": {
                    "filename.keyword": {
                        "value": q,
                        "boost": 30
                    }
                }
            },

            # 2️⃣ phrase match
            {
                "match_phrase": {
                    "filename": {
                        "query": q,
                        "boost": 15
                    }
                }
            },

            # 3️⃣ relevance match
            {
                "dis_max": {
                    "tie_breaker": 0.2,
                    "queries": [

                        {
                            "match": {
                                "filename": {
                                    "query": q,
                                    "boost": 5,
                                    "minimum_should_match": "70%"
                                }
                            }
                        },

                        {
                            "match": {
                                "filename.en": {
                                    "query": q,
                                    "boost": 5,
                                    "minimum_should_match": "70%"
                                }
                            }
                        },

                        {
                            "match": {
                                "path_recent": {
                                    "query": q,
                                    "boost": 3,
                                    "minimum_should_match": "50%"
                                }
                            }
                        },

                        {
                            "match": {
                                "path_real.tree": {
                                    "query": q,
                                    "boost": 2,
                                    "minimum_should_match": "30%"
                                }
                            }
                        }

                    ]
                }
            }
        ]

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

        bool_q: Dict[str, Any] = { "should": should, "minimum_should_match": 1}
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
