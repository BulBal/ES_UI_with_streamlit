# 팀별 ES 서버/웹앱 분리 구현 계획 (구현 전)

작성일: 2026-04-09  
기준 코드: `Pasted code_v3.py` (사용자 메모 반영: `app.py`는 범위 제외)

## 1. 목표와 범위

목표
- 스마트솔루션팀/디바이스팀을 **서로 다른 PC + 서로 다른 ES 클러스터**에서 독립 실행 가능하게 만든다.
- 현재 하드코딩된 팀 결합 지점을 설정 기반으로 분리한다.
- 기존 레이어(`config -> es_client -> dsl registry -> streamlit`)와 책임 분리를 유지한다.

범위 포함
- `Pasted code_v3.py` 실행 기준의 인덱스 목록/검색 동작 분리
- 인덱스 필터 정책의 하드코딩 제거
- 팀별 DSL 매핑 확장 포인트 마련
- 운영 설정 문서(`.env.example`, `README.md`) 동기화

범위 제외
- `app.py` 기능 수정
- 대규모 리팩토링(모듈 분해/구조 재편)
- 신규 API 서버/ORM 도입

## 2. 접근 방식 상세 설명

접근 원칙
- “PC별 독립 실행”을 전제로 **환경변수 중심 분리**를 우선한다.
- 팀 선택 UI를 새로 만들지 않고, 실행 환경에서 팀 컨텍스트를 고정한다.
- 기존 `EsClient`, `DslRegistry`를 재사용하고 중복 모듈 생성은 금지한다.

단계별 접근
1. 설정 계층 일반화
- 현재 `SERVICE_ALIAS_PREFIXES=("Smart_",)` 하드코딩을 `core/config.py`로 이동한다.
- `alias prefix`와 `허용 인덱스 allowlist`를 환경변수로 주입 가능하게 만든다.
- `AppConfig.request_verify`의 `es_ca_cert_path` 참조 불일치를 함께 정리한다.

2. ES 인덱스 목록 필터 분리
- `core/es_client.py`의 alias 필터를 설정 기반으로 변경한다.
- 필터 우선순위:
  - `ES_ALLOWED_INDICES`가 있으면 allowlist 우선
  - 없으면 `ES_ALIAS_PREFIXES` prefix 필터 적용
  - 둘 다 없으면 전체 alias 허용(운영 정책에 따라 기본값 지정)

3. DSL 선택 정책 확장
- `dsl/registry.py`에서 팀/인덱스 키 매핑을 명시적으로 확장한다.
- 디바이스팀 인덱스 스키마가 동일하면 기존 `DSLSmartSolutionDslBuilder` 재사용.
- 스키마가 다르면 `dsl/DSL_device_team.py` 신규 빌더 1개를 추가하고 registry에 연결.

4. 운영 엔트리(`Pasted code_v3.py`) 정합성 강화
- 선택 가능한 인덱스가 비어있거나 default 인덱스가 불일치할 때 fallback 동작을 명확화한다.
- 현재 DSL expander를 유지해 운영자가 실제 index/dsl 조합을 검증 가능하게 둔다.

5. 운영 설정 문서화
- `.env.example`에 팀 분리용 환경변수 추가
- `README.md`에 스마트팀/디바이스팀 실행 예시를 분리 기술

## 3. 수정될 파일 경로 목록

- `core/config.py`
- `core/es_client.py`
- `dsl/registry.py`
- `Pasted code_v3.py`
- `.env.example`
- `README.md`
- (조건부) `dsl/DSL_device_team.py`  ← 디바이스팀 스키마가 Smart와 다를 때만

## 4. 파일별 변경 내용

`core/config.py`
- `AppConfig`에 팀 분리 관련 설정 필드 추가
  - `es_alias_prefixes: tuple[str, ...]`
  - `es_allowed_indices: tuple[str, ...]`
  - `es_ca_cert_path: Optional[str]`
- `load_config()`에서 CSV 환경변수 파싱 유틸 사용
- `request_verify`가 `es_ca_cert_path`를 안전하게 참조하도록 정합성 보완

`core/es_client.py`
- 상수 `SERVICE_ALIAS_PREFIXES` 제거
- `list_indices()`가 `cfg.es_alias_prefixes`, `cfg.es_allowed_indices`를 사용하도록 변경
- 검색 호출(`search`)은 기존 유지

`dsl/registry.py`
- 팀별 인덱스 alias를 명시적으로 매핑
- 디바이스팀 인덱스 키를 registry에 추가
- default builder 정책을 문서화 가능한 형태로 고정

`Pasted code_v3.py`
- 운영 엔트리 기준으로 인덱스 선택 fallback 로직을 강화
- `cfg.es_default_index`가 실제 표시 목록과 불일치할 때 경고/대체 선택 처리
- 기존 검색/렌더링 흐름은 유지(레이어 침범 금지)

`.env.example`
- 팀 분리 실행을 위한 환경변수 추가
- 스마트팀/디바이스팀용 예시값 주석 제공

`README.md`
- 실행 시나리오를 2개로 분리 기술
  - Smart 팀 전용 실행
  - Device 팀 전용 실행
- 운영 파일이 `Pasted code_v3.py`임을 명시

`dsl/DSL_device_team.py` (조건부)
- 디바이스팀 필드 차이가 확인된 경우에만 추가
- 기존 `DslBuilder` 인터페이스 준수
- `any`/`unknown` 없이 타입 명시

## 5. 실제 코드베이스에 맞는 코드 스니펫

주의: 아래는 “계획용 스니펫”이며 아직 적용하지 않는다.

### 5-1. `core/config.py` 계획 스니펫
```python
from dataclasses import dataclass
from typing import Optional, Union

RequestsVerify = Union[bool, str]

def _csv(v: Optional[str]) -> tuple[str, ...]:
    if not v:
        return tuple()
    items = [x.strip() for x in v.split(",")]
    return tuple(x for x in items if x)

@dataclass(frozen=True)
class AppConfig:
    es_base_url: str
    es_default_index: str
    es_user: str
    es_pass: str
    es_verify_ssl: bool
    es_ca_cert_path: Optional[str]
    es_alias_prefixes: tuple[str, ...]
    es_allowed_indices: tuple[str, ...]
    default_size: int

    @property
    def request_verify(self) -> RequestsVerify:
        if not self.es_verify_ssl:
            return False
        if self.es_ca_cert_path:
            return self.es_ca_cert_path
        return True
```

### 5-2. `core/es_client.py` 계획 스니펫
```python
def list_indices(self) -> List[str]:
    url = f"{self.cfg.es_base_url.rstrip('/')}/_cat/aliases"
    r = requests.get(
        url,
        auth=HTTPBasicAuth(self.cfg.es_user, self.cfg.es_pass),
        params={"format": "json", "h": "alias"},
        verify=self.cfg.request_verify,
        timeout=10,
    )
    r.raise_for_status()
    rows = r.json() or []
    aliases = sorted({row.get("alias") for row in rows if row.get("alias")})

    if self.cfg.es_allowed_indices:
        return [a for a in aliases if a in self.cfg.es_allowed_indices]
    if self.cfg.es_alias_prefixes:
        return [a for a in aliases if a.startswith(self.cfg.es_alias_prefixes)]
    return aliases
```

### 5-3. `dsl/registry.py` 계획 스니펫
```python
class DslRegistry:
    def __init__(self):
        smart_builder = DSLSmartSolutionDslBuilder()
        self._map = {
            "Smart_Solution_Team": smart_builder,
            "smart_solution_docs": smart_builder,
            "Device_Team": smart_builder,  # 스키마 동일 시 재사용
        }
        self._default = smart_builder
```

### 5-4. `Pasted code_v3.py` 계획 스니펫
```python
index_options = idx_list if idx_list else [cfg.es_default_index]
prev_selected = st.session_state.get(IDX_KEY, cfg.es_default_index)
if prev_selected not in index_options:
    prev_selected = index_options[0]
    st.warning("설정된 기본 인덱스가 현재 접근 가능 목록에 없어 첫 번째 인덱스로 대체합니다.")
```

## 6. 고려한 대안과 트레이드오프

대안 A: PC별 환경변수 고정(권장)
- 장점: 코드 변경 최소, 운영 분리 명확, UI 복잡도 증가 없음
- 단점: 팀 전환 시 프로세스 재기동 필요

대안 B: 단일 앱에서 팀 선택 UI 추가
- 장점: 한 앱에서 다팀 운영 가능
- 단점: 권한 분리/오조작 리스크 증가, 현재 “PC별 독립” 요구와 불일치

대안 C: 팀별 앱 파일 분기(`Pasted code_v3_smart.py`, `..._device.py`)
- 장점: 즉시 분리 쉬움
- 단점: 코드 중복 급증, 유지보수 리스크 큼 (현재 금지사항 위반 가능성 높음)

결론
- 요구사항(팀별 다른 PC 독립 운영)과 현재 구조를 함께 만족하는 최적안은 A다.

## 7. 테스트/검증 방법

정적 검증
- 타입/문법: `py -3 -c "import ast, pathlib; ..."` 방식 AST 파싱
- 변경 파일 import 에러 확인

기능 검증 (Smart PC)
1. Smart 전용 env 설정 후 `streamlit run "Pasted code_v3.py"`
2. 사이드바 인덱스 목록이 Smart alias만 노출되는지 확인
3. 검색 실행 시 DSL/결과가 기존과 동일하게 동작하는지 확인

기능 검증 (Device PC)
1. Device 전용 env 설정 후 실행
2. 사이드바 인덱스 목록이 Device alias만 노출되는지 확인
3. 검색 결과/필터/페이지네이션/로컬 refine 기능이 동일 동작하는지 확인

예외 검증
- `ES_DEFAULT_INDEX`가 접근 불가 인덱스일 때 fallback 동작 확인
- SSL verify true/false + CA 경로 유무 조합 확인
- alias 조회 실패 시 기존 warning/fallback UI 유지 확인

회귀 검증
- 음성 입력, copy 버튼, refine filter, local pagination 동작 유지 확인

## 8. 롤백 전략

1차 롤백 (무중단 운영)
- 환경변수만 기존 값으로 복귀
- `ES_ALIAS_PREFIXES=Smart_` 단일값으로 되돌려 기존 동작 재현

2차 롤백 (코드)
- 변경 파일만 git revert
  - `core/config.py`
  - `core/es_client.py`
  - `dsl/registry.py`
  - `Pasted code_v3.py`
  - `.env.example`
  - `README.md`
  - (생성 시) `dsl/DSL_device_team.py`

롤백 기준
- 인덱스 목록 미노출
- 검색 5xx/4xx 급증
- 운영팀에서 팀간 인덱스 혼선 발견

## 9. 완료 조건

- `Pasted code_v3.py` 실행 기준으로 Smart/Device가 각 PC에서 독립 동작
- 인덱스 목록 필터가 하드코딩(`"Smart_"`)에 의존하지 않음
- DSL 선택이 팀별 인덱스 정책과 일치
- 기존 기능(검색, refine, 페이지네이션, 음성 입력, 경로 복사) 회귀 없음
- 운영 문서(`.env.example`, `README.md`)로 재현 가능한 실행 절차 제공
- 구현 코드에 `any`, `unknown` 미사용

