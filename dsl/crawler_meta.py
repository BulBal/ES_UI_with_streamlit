from typing import Any, Dict, List, Optional
from dsl.base import DslBuilder, SearchParams

FIELD_TO_ES = {
    "title":    ["title^3", "title.partial^2"],
    "filename": ["filename^2", "filename.partial^2"],
    "author":   ["author", "author.partial"],
    "keywords": ["keywords", "keywords.partial"],
    "path":     ["path_tree^4"],
}

class CrawlerMetaDslBuilder(DslBuilder):
    def build(self, p: SearchParams) -> Dict[str, Any]:
        page = max(1, p.page)
        size = min(max(1, p.size), 50)
        from_ = (page - 1) * size

        selected = p.selected_fields or ["title", "filename", "path"]

        fields: List[str] = []
        for k in selected:
            fields.extend(FIELD_TO_ES.get(k, []))
        if not fields:
            fields = ["title^3", "filename^2", "path_tree^4"]

        should = [
            {"term": {"title.keyword": {"value": p.q, "boost": 8}}},
            {"term": {"filename.keyword": {"value": p.q, "boost": 6}}},
        ]

        must = [{
            "multi_match": {
                "query": p.q,
                "fields": fields,
                "type": "best_fields",
                "operator": "or",
                "minimum_should_match": "2<75%"
            }
        }]

        filters: List[Dict[str, Any]] = []
        if p.extension:
            filters.append({"term": {"extension": p.extension.lower()}})

        if p.created_from or p.created_to:
            rng: Dict[str, Any] = {}
            if p.created_from: rng["gte"] = p.created_from.isoformat()
            if p.created_to:   rng["lte"] = p.created_to.isoformat()
            filters.append({"range": {"created_at": rng}})

        if p.modified_from or p.modified_to:
            rng: Dict[str, Any] = {}
            if p.modified_from: rng["gte"] = p.modified_from.isoformat()
            if p.modified_to:   rng["lte"] = p.modified_to.isoformat()
            filters.append({"range": {"modified_at": rng}})

        bool_q: Dict[str, Any] = {"must": must, "should": should, "minimum_should_match": 0}
        if filters:
            bool_q["filter"] = filters

        dsl: Dict[str, Any] = {
            "track_total_hits": True,
            "from": from_,
            "size": size,
            "_source": [
                "title", "filename", "path_virtual", "path_real",
                "extension", "created_at", "modified_at",
                "filesize_bytes", "content_type", "source_index"
            ],
            "query": {"bool": bool_q},
            "highlight": {
                "pre_tags": ["<mark>"],
                "post_tags": ["</mark>"],
                "require_field_match": False,
                "fields": {
                    "title": {"number_of_fragments": 0},
                    "filename": {"number_of_fragments": 0},
                }
            },
            "sort": [{"modified_at": {"order": "desc"}}] if p.sort == "RECENCY" else [{"_score": {"order": "desc"}}],
        }
        return dsl
