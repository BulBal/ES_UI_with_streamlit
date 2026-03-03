import os
from dataclasses import dataclass
from typing import Optional, Union

def _bool(v: str, default: bool = False) -> bool:
    if v is None:
        return default
    return v.lower() in ("1", "true", "yes", "y", "on")

def _int(v: str, default: int) -> int:
    try:
        return int(v)
    except Exception:
        return default

RequestsVerify = Union[bool, str]

@dataclass(frozen=True)
class AppConfig:
    es_base_url: str
    es_default_index: str
    es_user: str
    es_pass: str
    es_verify_ssl: bool
    es_ca_cert_path: Optional[str] = None
    default_size: int = 30
    @property
    def request_verify(self) -> RequestsVerify:
        if not self.es_verify_ssl:
            return False
        if self.es_ca_cert_path:
            return self.es_ca_cert_path
        return True

def load_config() -> AppConfig:
    # 운영(Linux)에서 표준은 환경변수 주입이므로 os.environ을 소스로 삼는다.
    return AppConfig(
        es_base_url=os.getenv("ES_BASE_URL", "https://localhost:9200"),
        es_default_index=os.getenv("ES_INDEX", "sms_search_v1_001"),
        es_user=os.getenv("ES_USER", "elastic"),
        es_pass=os.getenv("ES_PASS", "changeme"),
        es_verify_ssl=_bool(os.getenv("ES_VERIFY_SSL"), default=True),
        es_ca_cert_path=os.getenv("ES_CA_CERT", default="c:/elastic/certs/http_ca.crt"),
        default_size=_int(os.getenv("ES_PAGE_SIZE"), default=30),
    )
