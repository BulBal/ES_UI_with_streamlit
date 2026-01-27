# PMC Search - Streamlit Single App (ES 검색 단일앱)

이 프로젝트는 **Streamlit 단일 앱**으로
- 검색 UI(프론트)
- Elasticsearch 호출(백엔드 역할)
을 한 프로세스에서 수행하는 실습용 베이스라인입니다.

## 1) 요구사항/전제
- Elasticsearch: 로컬 단일 노드 (Windows 가능)
- 인덱스: `pmc_search_v1`
- 인증: 실습용 BasicAuth(예: elastic 계정)
- ES 8.x는 기본이 https + self-signed일 수 있으므로 `ES_VERIFY_SSL` 옵션 제공

## 2) 설치
```bash
python -m venv .venv
# Windows PowerShell
.venv\Scripts\Activate.ps1

pip install -r requirements.txt
```

## 3) 실행 (PowerShell 예시)
> Streamlit은 기본적으로 .env를 자동 로드하지 않습니다.  
> 실습에선 아래처럼 환경변수로 주는 게 가장 간단합니다.

```powershell
$env:ES_BASE_URL="https://localhost:9200"
$env:ES_INDEX="pmc_search_v1"
$env:ES_USER="elastic"
$env:ES_PASS="비번"
$env:ES_VERIFY_SSL="false"   # self-signed면 false
streamlit run app.py
```

## 4) DSL 템플릿(현재 적용)
- multi_match: title^3, filename^2, body, path_virtual, path_real
- filter(옵션): extension.keyword, created_at range
- highlight: title(전체), body(fragment 2개)

## 5) 다음 확장 아이디어(추천 순)
1) “최근 3개월/1주일/어제” 같은 자연어 → date range 자동 변환
2) extension 자동 인식 (예: “pdf”, “pptx” 등)
3) path_virtual prefix 필터(폴더 범위 검색)
4) 페이징 UI 개선(Next/Prev)
5) 결과 클릭 시 다운로드 API (운영 시엔 path_real 숨기고 fileId 기반으로)