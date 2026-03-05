from dsl.base import DslBuilder
from dsl.crawler_meta import CrawlerMetaDslBuilder
from dsl.crawler_fulltext import CrawlerFulltextDslBuilder
from dsl.crawler_smart_solution import CrawlerSmartSolutionDslBuilder


class DslRegistry:
    """
    인덱스별 DSL 빌더 매핑.
    - 팀별 인덱스가 늘어나면 여기만 추가.
    - 또는 prefix 룰로 자동 매핑도 가능.
    """
    def __init__(self):
        self._map = {
            # 기본 메타 검색용
            "d_crawler_search": CrawlerMetaDslBuilder(),
            "pmc_search_meta_v1": CrawlerMetaDslBuilder(),
            "Smart_Solution_Team": CrawlerSmartSolutionDslBuilder(),
            # 본문 포함 인덱스 예시
            "pmc_search_fulltext_v1": CrawlerFulltextDslBuilder(),
        }

        self._default = CrawlerSmartSolutionDslBuilder()

    def get(self, index: str) -> DslBuilder:
        return self._map.get(index, self._default)
