from typing import Any, Dict, List
from dsl.base import DslBuilder, SearchParams

class CrawlerFulltextDslBuilder(DslBuilder):
    def build(self, p: SearchParams) -> Dict[str, Any]:
        page = max(1, p.page)
        size = min(max(1, p.size), 50)
        from_ = (page - 1) * size

        must = [{
            "multi_match": {
                "query": p.q,
                "fields": ["title^3", "filename^2", "body", "path_tree^3"],
                "type": "best_fields",
                "operator": "or",
                "minimum_should_match": "2<75%"
            }
        }]

        dsl: Dict[str, Any] = {
            "track_total_hits": True,
            "from": from_,
            "size": size,
            "_source": ["title","filename","path_virtual","path_real","extension","created_at","modified_at","filesize_bytes","content_type","source_index"],
            "query": {"bool": {"must": must}},
            "highlight": {
                "pre_tags": ["<mark>"],
                "post_tags": ["</mark>"],
                "require_field_match": False,
                "fields": {
                    "title": {"number_of_fragments": 0},
                    "body": {"fragment_size": 180, "number_of_fragments": 2}
                }
            }
        }
        return dsl
