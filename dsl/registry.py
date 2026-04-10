from dsl.base import DslBuilder
from dsl.crawler_meta import CrawlerMetaDslBuilder
from dsl.crawler_fulltext import CrawlerFulltextDslBuilder
from dsl.DSL_smart_solution import DSLSmartSolutionDslBuilder


class DslRegistry:
    """
    Index-to-DSL builder mapping.
    """

    def __init__(self):
        meta_builder = CrawlerMetaDslBuilder()
        fulltext_builder = CrawlerFulltextDslBuilder()
        smart_builder = DSLSmartSolutionDslBuilder()

        self._map = {
            # Meta indices
            "d_crawler_search": meta_builder,
            "pmc_search_meta_v1": meta_builder,

            # Smart/Device team indices (shared schema)
            "Smart_Solution_Team": smart_builder,
            "smart_solution_docs": smart_builder,
            "Device_Team": smart_builder,
            "device_team_docs": smart_builder,

            # Fulltext index example
            "pmc_search_fulltext_v1": fulltext_builder,
        }

        self._default = smart_builder

    def get(self, index: str) -> DslBuilder:
        return self._map.get(index, self._default)
