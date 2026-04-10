# Team/Cluster 분리 준비 리서치 (코드베이스 기반)

작성일: 2026-04-09  
범위: `app.py`, `Pasted code_v3.py`, `core/*`, `dsl/*`, 운영 관련 설정 파일

## 0. 조사 범위와 전제
- 사용자 메모 : 현재 사용중인 운영/개발 파일은 'Pasted code_v3.py' 이다 따라서 app.py는 추후 과정에서 배제 한다.
- 본 문서는 **구현 제안이 아니라 현재 코드 구조/동작의 사실관계 정리**다.
- 모든 항목은 코드 근거를 함께 표기했다.
- 확인한 실행 엔트리는 Streamlit 스크립트(`app.py`, `Pasted code_v3.py`)다.

사용자 메모 : 실제 운영 엔트리 파일은 pasted code_v3.py이다 무조건 이 파일을 기준으로 생각하면된다.
---

## 1. 현재 시스템 구조와 주요 레이어

### 1-1. 레이어 구성
| 레이어 | 역할 | 근거 |
|---|---|---|
| UI/오케스트레이션 | Streamlit 화면 구성, 입력 수집, 세션 상태, DSL 호출 및 결과 렌더링 | `app.py:21-23, 200-591`, `Pasted code_v3.py:24-27, 329-1103` |
| 설정(Config) | ES URL/기본 인덱스/인증/페이지 크기 로딩 | `core/config.py:20-46` |
| 인프라 클라이언트 | ES `_search`, `_cat/aliases` HTTP 호출 및 응답 매핑 | `core/es_client.py:12-66` |
| DSL 빌더 계층 | 인덱스별 DSL 생성 전략 | `dsl/base.py:8-27`, `dsl/registry.py:7-26`, `dsl/DSL_smart_solution.py`, `dsl/crawler_meta.py`, `dsl/crawler_fulltext.py` |
| 모델 | ES 히트 DTO | `core/models.py:4-14` |
| 데이터프레임 유틸 | hit raw -> row/df 변환 | `core/df_builder.py:15-57` |

### 1-2. 구조적 특징
- `app.py`/`Pasted code_v3.py`는 모듈 로드 시점에 `cfg`, `es`, `dsl_registry`를 전역으로 생성한다.  
  근거: `app.py:21-23`, `Pasted code_v3.py:24-27`
- 인덱스 선택은 ES alias 조회 결과에 의존한다.  
  근거: `app.py:197-198, 255-271`, `core/es_client.py:52-66`
- 인덱스별 DSL 선택은 `DslRegistry`의 문자열 key 매핑 방식이다.  
  근거: `dsl/registry.py:14-26`

---

## 2. 관련 기능의 실제 동작 흐름

### 2-1. 현재 `app.py` 검색 흐름
1. 앱 시작 시 설정/클라이언트/레지스트리 생성  
   근거: `app.py:21-23`
2. 사이드바에서 조회 가능한 인덱스 목록 로드  
   근거: `app.py:252-271` -> `fetch_accessible_indices()` -> `es.list_indices()` (`app.py:197-198`, `core/es_client.py:52-66`)
3. 검색 버튼 클릭 시 `should_search=True`, `page=1`  
   근거: `app.py:235-240`
4. `should_search` 분기에서 `selected_index`로 DSL builder 선택 후 `SearchParams` 생성  
   근거: `app.py:322-338`
5. `builder.build(params)` 결과 DSL로 ES 검색 수행  
   근거: `app.py:338, 347`
6. hit를 row로 수동 변환 후 DataFrame 생성, 날짜/파일크기 후처리  
   근거: `app.py:348-385`
7. AgGrid 렌더링 + 페이지네이션 버튼으로 `page` 갱신 후 `st.rerun()`  
   근거: `app.py:392-574`

### 2-2. `Pasted code_v3.py` 추가 흐름
- `app.py` 기본 흐름 + 음성 입력 + 로컬 2차 정제(snapshot 기반)가 추가됨.
- 검색 시 ES에서 `RESULT_SNAPSHOT_SIZE=3000`건을 1차 수집한 뒤 세션에 저장한다.  
  근거: `Pasted code_v3.py:119, 780-853`
- 이후 로컬 필터(`apply_refine_filter`) + 로컬 페이지네이션(`paginate_local_df`)으로 결과를 재가공한다.  
  근거: `Pasted code_v3.py:121-167, 896-930, 1094-1103`

---

## 3. 핵심 엔트리 포인트와 호출 체인

### 3-1. 엔트리 포인트
- `streamlit run app.py`  
- (대안/실험) `streamlit run "Pasted code_v3.py"`

### 3-2. 호출 체인 A: 인덱스 목록
`fetch_accessible_indices()`  
-> `EsClient.list_indices()`  
-> `GET {ES_BASE_URL}/_cat/aliases?format=json&h=alias`  
-> alias를 `SERVICE_ALIAS_PREFIXES=("Smart_",)`로 필터링  
근거: `app.py:197-198`, `core/es_client.py:10,52-66`

### 3-3. 호출 체인 B: 검색 실행
검색 버튼  
-> `st.session_state.should_search=True`  
-> `DslRegistry.get(selected_index)`  
-> `DslBuilder.build(SearchParams)`  
-> `EsClient.search(index, dsl)`  
-> ES 응답을 `EsHit` 리스트로 변환  
-> Streamlit grid 렌더  
근거: `app.py:235-240, 322-347`, `dsl/registry.py:25-26`, `core/es_client.py:16-48`

### 3-4. 호출 체인 C: v3 로컬 정제
ES 1차 snapshot 검색  
-> `result_snapshot_df`/`working_result_df` 세션 저장  
-> `apply_refine_filter()`로 로컬 재필터  
-> `paginate_local_df()`로 로컬 페이지 렌더  
근거: `Pasted code_v3.py:780-853, 896-930`

---

## 4. 수정 시 영향받는 파일 경로

### 4-1. 팀별 서버/웹앱 분리와 직접 연관 (우선 확인 대상)
- `core/config.py`  
  이유: ES 서버 주소/기본 인덱스/인증/SSL 설정의 단일 진입점 (`core/config.py:36-45`)
- `core/es_client.py`  
  이유: alias prefix 하드코딩 필터, 검색 호출 구현 (`core/es_client.py:10,16-25,52-66`)
- `dsl/registry.py`  
  이유: 인덱스명 -> DSL 빌더 매핑 하드코딩 (`dsl/registry.py:14-23`)
- `dsl/DSL_smart_solution.py`  
  이유: Smart Solution 인덱스 필드 스키마 가정 (`dsl/DSL_smart_solution.py:4-14,122-147`)
- `app.py` 또는 `Pasted code_v3.py`  
  이유: 인덱스 선택/검색 파라미터/UI 상태 관리 (`app.py:252-338`, `Pasted code_v3.py:710-799`)

### 4-2. 간접 연관 (운영 문서/설정 동기화)
- `.env.example` (`.env.example:2-9`)
- `README.md` (`README.md:28-33`)
- 크롤러/템플릿 파일  
  - `crawler세팅 버전/_settings_v1.yaml` (클러스터 URL/인덱스/크롤링 경로 하드코딩 포함, `...:197-205`)
  - `template 확인용/smart-solution-template-v1.json` (인덱스 패턴/필드 정의, `...:3-5,77-125`)

---

## 5. 기존 코드 관례와 암묵적 규칙

### 5-1. 명시적 관례
- 설정/도메인은 dataclass 사용 (`AppConfig`, `EsHit`)  
  근거: `core/config.py:19-27`, `core/models.py:4-14`
- DSL 빌더 인터페이스(`DslBuilder`) + 레지스트리 매핑 패턴 사용  
  근거: `dsl/base.py:21-27`, `dsl/registry.py:7-26`
- 필터 입력값은 소문자/중복 제거 정규화 후 사용  
  근거: `app.py:126-139`, `Pasted code_v3.py:180-193`

### 5-2. 암묵적 규칙
- “접근 가능한 인덱스”의 정의가 alias prefix `"Smart_"`에 묶여 있음  
  근거: `core/es_client.py:10,65`
- 인덱스별 동작 분기는 **인덱스 문자열 이름**에 강하게 결합  
  근거: `dsl/registry.py:14-20`
- 정렬 UI는 사실상 relevance만 허용(RECENCY 경로는 DSL에만 존재)  
  근거: `app.py:282`, `dsl/DSL_smart_solution.py:143-147`
- ORM 레이어는 현재 코드베이스에 존재하지 않음(요청하신 ORM 관례는 “해당 없음”으로 해석 필요)

---

## 6. 중복 구현 위험 지점

- `app.py`와 `Pasted code_v3.py`에 검색/렌더링 코드가 대량 중복됨  
  근거: 공통 import/세션/검색/AgGrid 블록 (`app.py:21-574`, `Pasted code_v3.py:24-1103`)
- `core/df_builder.py` 함수를 import하지만 실제 검색 경로에서 수동 row 변환을 별도로 구현  
  근거: import (`app.py:19`, `Pasted code_v3.py:22`), 수동 변환 (`app.py:348-370`, `Pasted code_v3.py:811-833`)
- 확장자 도움말/파싱/페이지네이션 유틸이 파일별 복제 상태  
  근거: `app.py:34-194`, `Pasted code_v3.py:37-193,279-323`

---

## 7. 리팩토링 없이 기능 추가 가능한 지점

아래는 “현재 구조를 유지한 채” 확장 가능한 삽입 지점이다.

- 환경 분리(PC별/팀별 실행값)는 이미 `ES_BASE_URL`, `ES_INDEX`, `ES_USER`, `ES_PASS`로 주입 가능  
  근거: `core/config.py:39-44`, `.env.example:2-5`
- 인덱스별 DSL 추가는 `dsl/registry.py`의 `_map`에 key 추가 방식으로 가능  
  근거: `dsl/registry.py:14-20`
- 팀별 필드 차이가 있으면 새 DSL 빌더 파일을 추가하고 registry에 연결하는 방식이 기존 패턴과 일치  
  근거: `dsl/base.py:21-27`, `dsl/registry.py:1-4,14-20`
- UI에서 실제 검색 대상 인덱스 선택은 이미 selectbox로 동작하므로, 목록 필터 정책만 맞추면 동일 UI 재사용 가능  
  근거: `app.py:266-271`, `Pasted code_v3.py:724-729`

주의: 현재 상태에서는 alias prefix 하드코딩, builder 매핑 하드코딩 때문에 “팀 분리”가 자동으로 되지 않는다.

---

## 8. 장애 가능성, 성능 리스크, 운영 리스크

### 8-1. 기능 장애 가능성 (코드상 확인됨)
- `AppConfig.request_verify`가 존재하지 않는 `self.es_ca_cert_path`를 참조  
  근거: `core/config.py:31-32`  
  영향: `ES_VERIFY_SSL=true` 경로에서 `AttributeError` 가능.

- `SearchParams`에 `selected_fields`가 없는데 `CrawlerMetaDslBuilder`가 접근  
  근거: `dsl/base.py:9-19` vs `dsl/crawler_meta.py:16`  
  영향: 해당 builder 경로 사용 시 예외 가능.

- `CrawlerMetaDslBuilder`는 `params.extension.lower()`를 호출하지만 UI는 list를 전달  
  근거: `dsl/crawler_meta.py:41-42`, `app.py:294`, `Pasted code_v3.py:752`  
  영향: `list` 입력 시 타입 오류 가능.

- `list_indices`가 `"Smart_"` prefix만 통과시켜 타 팀 alias는 UI에서 보이지 않을 수 있음  
  근거: `core/es_client.py:10,65`

### 8-2. 성능 리스크
- v3는 검색 1회마다 최대 3000건을 세션 DataFrame으로 유지하고 로컬 재필터링 수행  
  근거: `Pasted code_v3.py:119, 787-790, 849-853, 896-930`  
  영향: 메모리/응답 지연 증가 가능.

- 페이지 전환마다 Streamlit rerun 발생 (네트워크 재호출 또는 로컬 재계산)  
  근거: `app.py:571-574`, `Pasted code_v3.py:1101-1103`

### 8-3. 운영 리스크
- 기본값에 실제 서버 IP/기본 계정 문자열이 포함되어 있음  
  근거: `core/config.py:39-42`
- alias 목록 캐시 TTL 30초로 짧은 지연 동기화가 발생 가능  
  근거: `app.py:196`, `Pasted code_v3.py:325`
- 크롤러 설정 파일에 URL/인덱스/계정정보가 하드코딩되어 있음  
  근거: `crawler세팅 버전/_settings_v1.yaml:7,200-205`

---

## 9. 불확실하거나 추가 확인이 필요한 부분

- 실제 운영 엔트리 파일이 `app.py`인지 `Pasted code_v3.py`인지 확정 필요.  
  이유: 두 파일 모두 실행 가능 구조이며 기능 범위가 다름.

  사용자 메모 : 실제 운영 엔트리 파일은 pasted code_v3.py이다 무조건 이 파일을 기준으로 생각하면된다.

- 디바이스팀 인덱스의 필드 스키마가 Smart Solution과 동일한지 확인 필요.  
  이유: DSL이 `filename.noun`, `path_recent`, `path_real.tree` 등 특정 필드를 전제 (`dsl/DSL_smart_solution.py:4-14`).

- 디바이스팀 alias 명명 규칙 확인 필요.  
  이유: 현재 필터는 `"Smart_"` 고정 (`core/es_client.py:10,65`).

- 팀 분리 목표가 “각 PC에서 독립 앱 1개”인지, “단일 앱에서 팀 전환 가능”인지 정책 확인 필요.  
  이유: 영향 파일 범위가 달라짐(`core/config.py` 단순 분리 vs `app.py` 다중 프로파일 UI 추가).

- SSL 인증서 검증 방식(자체서명 CA 경로 사용 여부) 확인 필요.  
  이유: 현재 `es_ca_cert_path` 참조 불일치 존재(`core/config.py:31-32`).

---

## 부록 A. 관찰된 불일치/정리 포인트 (사실만)
- `core/df_builder.py`는 현재 검색 주경로에서 사용되지 않는다.  
  근거: import만 존재 (`app.py:19`, `Pasted code_v3.py:22`), 함수 호출 없음.
- `DslRegistry`의 default builder가 `DSLSmartSolutionDslBuilder`다.  
  근거: `dsl/registry.py:23,26`
- `app.py`에는 선택 경로 표시 `text_input` 블록이 중복 배치되어 있다.  
  근거: `app.py:547-553` 및 `app.py:556-562`

