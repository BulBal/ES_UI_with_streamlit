import os
import json
import math
from dataclasses import dataclass
from datetime import date
from typing import Any, Dict, List, Optional, Tuple

import requests
import streamlit as st
from requests.auth import HTTPBasicAuth

# =========================
# ✅ Config (ENV 우선)
# =========================
ES_BASE_URL = os.getenv("ES_BASE_URL", "https://localhost:9200")   # ES 8.x는 보통 https + self-signed
ES_INDEX    = os.getenv("ES_INDEX", "pmc_search_v1")
ES_USER     = os.getenv("ES_USER", "elastic")
ES_PASS     = os.getenv("ES_PASS", "changeme")
ES_VERIFY_SSL = os.getenv("ES_VERIFY_SSL", "false").lower() in ("1", "true", "yes")  # 실습: self-signed면 false
DEFAULT_SIZE = int(os.getenv("ES_PAGE_SIZE", "10"))

SEARCH_FIELD_OPTIONS = [
    ("title", "제목"),
    ("filename", "파일명"),
    ("body", "본문"),
    ("path_real", "경로(실제)"),
    ("path_virtual", "경로(가상)"),
    ("author", "작성자"),
    ("keywords", "키워드"),
]
# =========================
# ✅ Query Builder (DSL 템플릿)
# =========================
def build_dsl(
    q: str,
    page: int,
    size: int,
    sort: str,
    extension: Optional[str],
    created_from: Optional[date],
    created_to: Optional[date],
    modified_from: Optional[date],   # ✅ 추가
    modified_to: Optional[date],
) -> Dict[str, Any]:
    """
    Streamlit 단일앱에서 '자연어 입력 문자열(q)'을 ES Query DSL로 변환하는 최소 템플릿.
    - multi_match: title^3, filename^2, body, path_virtual, path_real
    - filter: extension.keyword, created_at range (옵션)
    - highlight: title(전체), body(fragment)
    """
    page = max(1, page)
    size = min(max(1, size), 50)  # 과도한 응답 방지(실습용 상한)
    from_ = (page - 1) * size

    must = [{
        "multi_match": {
            "query": q,
            "fields": ["title^3", "filename^2", "body", "path_virtual", "path_real"],
            "type": "best_fields",
            "operator": "and"
        }
    }]

    filters: List[Dict[str, Any]] = []
    if extension:
        # mapping에 extension.keyword가 존재하므로 keyword로 term 필터 권장
        filters.append({"term": {"extension.keyword": extension.lower()}})

    if created_from or created_to:
        rng: Dict[str, Any] = {}
        if created_from:
            rng["gte"] = created_from.isoformat()
        if created_to:
            rng["lte"] = created_to.isoformat()
        filters.append({"range": {"created_at": rng}})
    
    if modified_from or modified_to:
        rng: Dict[str, Any] = {}
        if modified_from:
            rng["gte"] = modified_from.isoformat()
        if modified_to:
            rng["lte"] = modified_to.isoformat()
        filters.append({"range": {"modified_at": rng}})

    query: Dict[str, Any] = {"bool": {"must": must, "filter": filters}} if filters else {"bool": {"must": must}}

    dsl: Dict[str, Any] = {
        "track_total_hits": True,
        "from": from_,
        "size": size,
        "_source": [
            "title", "filename", "path_virtual", "path_real",
            "extension", "created_at", "modified_at", "filesize_bytes", "content_type", "source_index"
        ],
        "query": query,
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

    if sort == "RECENCY":
        dsl["sort"] = [{"modified_at": {"order": "desc"}}]

    return dsl



# =========================
# ✅ ES Client
# =========================
@dataclass
class EsHit:
    id: str
    score: float
    title: str
    filename: str
    path_virtual: str
    path_real: str
    extension: str
    created_at: str
    modified_at: str
    filesize_bytes: int
    highlights: Dict[str, List[str]]

def es_search(dsl: Dict[str, Any]) -> Tuple[int, List[EsHit]]:
    """ES REST API 호출. (실습: BasicAuth, verify 옵션 제공)"""
    url = f"{ES_BASE_URL.rstrip('/')}/{ES_INDEX}/_search"
    r = requests.post(
        url,
        auth=HTTPBasicAuth(ES_USER, ES_PASS),
        headers={"Content-Type": "application/json"},
        data=json.dumps(dsl),
        verify=ES_VERIFY_SSL,
        timeout=15,
    )
    r.raise_for_status()
    payload = r.json()

    total = int(payload.get("hits", {}).get("total", {}).get("value", 0))
    hits_raw = payload.get("hits", {}).get("hits", [])

    hits: List[EsHit] = []
    for h in hits_raw:
        src = h.get("_source", {}) or {}
        hl = h.get("highlight", {}) or {}
        hits.append(EsHit(
            id=str(h.get("_id", "")),
            score=float(h.get("_score", 0.0) or 0.0),
            title=str(src.get("title", "") or ""),
            filename=str(src.get("filename", "") or ""),
            path_virtual=str(src.get("path_virtual", "") or ""),
            path_real=str(src.get("path_real", "") or ""),
            extension=str(src.get("extension", "") or ""),
            created_at=str(src.get("created_at", "") or ""),
            modified_at=str(src.get("modified_at", "") or ""),
            filesize_bytes=int(src.get("filesize_bytes", 0) or 0),
            highlights={k: [str(x) for x in v] for k, v in hl.items()}
        ))

    return total, hits
# =========================
#✅ 페이지 바 UI 함수 (붙여넣기)
# =========================
def render_pagination(total: int, page: int, size: int, window: int = 7) -> int:
    """
    - total: 전체 hit 수
    - page: 현재 페이지(1-base)
    - size: 페이지 크기
    - window: 한 번에 보여줄 페이지 버튼 개수(홀수 추천)
    반환: 사용자가 클릭한 새 page (변경 없으면 기존 page)
    """
    if total <= 0:
        return page

    total_pages = max(1, math.ceil(total / size))
    page = max(1, min(page, total_pages))

    # prev/next
    c1, c2, c3 = st.columns([1, 6, 1])
    with c1:
        prev_disabled = page <= 1
        if st.button("◀ 이전", disabled=prev_disabled, use_container_width=True, key="pg_prev"):
            return page - 1

    # 가운데: 숫자 버튼들
    with c2:
        # 윈도우 범위 계산
        half = window // 2
        start = max(1, page - half)
        end = min(total_pages, start + window - 1)
        start = max(1, end - window + 1)

        cols = st.columns(min(window + 4, 30))  # 너무 많은 columns 방지

        i = 0
        def page_btn(p: int, label: str = None):
            nonlocal i
            label = label or str(p)
            is_current = (p == page)
            # 현재 페이지는 disabled로 표시(클릭 방지)
            if cols[i].button(label, disabled=is_current, use_container_width=True, key=f"pg_{p}"):
                return p
            i += 1
            return None

        # 1 ... 표시
        newp = None
        if start > 1:
            newp = page_btn(1, "1")
            if newp: return newp
            if start > 2:
                # dots (버튼 대신 텍스트)
                cols[i].markdown("<div style='text-align:center; padding-top:8px;'>…</div>", unsafe_allow_html=True)
                i += 1

        # start~end
        for p in range(start, end + 1):
            newp = page_btn(p)
            if newp: return newp

        # ... last 표시
        if end < total_pages:
            if end < total_pages - 1:
                cols[i].markdown("<div style='text-align:center; padding-top:8px;'>…</div>", unsafe_allow_html=True)
                i += 1
            newp = page_btn(total_pages, str(total_pages))
            if newp: return newp

        st.caption(f"{page} / {total_pages} 페이지 · 총 {total:,}건")

    with c3:
        next_disabled = page >= total_pages
        if st.button("다음 ▶", disabled=next_disabled, use_container_width=True, key="pg_next"):
            return page + 1

    return page

# =========================
# ✅ ES: 접근 가능한 인덱스 목록 가져오기 (UI 표시용)
# =========================
@st.cache_data(ttl=30)
def fetch_accessible_indices() -> List[str]:
    """
    현재 ES 계정으로 '보이는' 인덱스 목록을 가져온다.
    - 권한이 없으면 일부만 보이거나 에러날 수 있음(그게 정상)
    """
    url = f"{ES_BASE_URL.rstrip('/')}/_cat/indices"
    r = requests.get(
        url,
        auth=HTTPBasicAuth(ES_USER, ES_PASS),
        params={"format": "json", "h": "index"},
        verify=ES_VERIFY_SSL,
        timeout=10,
    )
    r.raise_for_status()
    rows = r.json() or []
    names = sorted({row.get("index") for row in rows if row.get("index")})
    return names
# =========================
# ✅ UI
# =========================
st.set_page_config(page_title="PMC Search (Streamlit)", layout="wide")

st.title("PMC 문서 검색 (Streamlit 단일 앱)")
st.caption("자연어 입력 → 파이썬에서 DSL 생성 → ES 검색 → 결과 리스트/하이라이트 출력")

with st.sidebar:
    st.subheader("검색 가능한 인덱스(읽기 전용)")

    try:
        idx_list = fetch_accessible_indices()
        st.caption(f"총 {len(idx_list)}개 (현재 계정 권한 기준)")
        # ✅ 보기만: 멀티셀렉트로 보여주되 선택값은 아직 사용 안 함
        st.multiselect(
            "인덱스 목록",
            options=idx_list,
            default=[ES_INDEX] if ES_INDEX in idx_list else [],
            help="지금은 UI 표시만 합니다. (선택해도 검색 대상은 아직 고정)",
            key="ui_index_view",
        )
    except requests.HTTPError as e:
        st.warning("인덱스 목록을 불러오지 못했습니다(권한/설정 확인 필요).")
        st.code(str(e))
    except Exception as e:
        st.warning("인덱스 목록 조회 중 오류가 발생했습니다.")
        st.code(str(e))

    st.divider()
    st.subheader("검색 옵션")
    if "size" not in st.session_state:
        st.session_state.size = DEFAULT_SIZE

    size = st.number_input("페이지 크기", min_value=1, max_value=50, value=int(st.session_state.size), step=1)
    if int(size) != int(st.session_state.size):
        st.session_state.size = int(size) 
        st.session_state.page = 1  # ✅ size 바뀌면 1페이지로

    # ✅ session_state에 page 유지 (없으면 1로 초기화)
    if "page" not in st.session_state:
        st.session_state.page = 1

    page = st.number_input("페이지", min_value=1, value=int(st.session_state.page), step=1)
    # 사용자가 number_input을 바꾸면 state도 동기화
    st.session_state.page = int(page)
    sort = st.selectbox("정렬", options=["RELEVANCE", "RECENCY"], index=0)

    st.divider()
    st.subheader("필터 (옵션)")
    extension = st.text_input("확장자(extension)", placeholder="예: pdf / docx / pptx ...").strip() or None

    st.subheader("생성일 필터 (옵션)")
    c1, c2 = st.columns(2)
    created_from = c1.date_input("created_from", value=None)
    created_to = c2.date_input("created_to", value=None)

    use_date_filter = st.checkbox("날짜 필터 사용", value=False)
    if not use_date_filter:
        created_from = None
        created_to = None
    
    st.subheader("수정일 필터 (옵션)")

    m1, m2 = st.columns(2)
    modified_from = m1.date_input("modified_from", value=None)
    modified_to = m2.date_input("modified_to", value=None)

    use_modified_filter = st.checkbox("수정일 필터 사용", value=False)
    if not use_modified_filter:
        modified_from = None
        modified_to = None

# =========================
# ✅ Search Mode Selector (자연어 only)
# =========================
label_by_key = dict(SEARCH_FIELD_OPTIONS)
keys = list(label_by_key.keys())

# ✅ 위젯 key 고정 + session_state 정합성 보정
# UI 위젯에서 field들의 상태들을 저장 할 ID(이름)을 지정 -> ms_key
ms_key = "ui_selected_fields"
prev = st.session_state.get(ms_key, ["title", "filename", "body"]) # ms_key에 Default 값을 입력
prev = [x for x in prev if x in keys] #options 안에 존재하는 값들만 있게끔 강제하는 과정
if not prev:
    prev = ["title", "filename"] # 선택된게 아무것도 없다면 Default로 2개를 띄움

selected_fields = st.multiselect(
    "검색 대상 필드 (현재는 아무런 기능을 하지 않음 -> 추후 가중치 추가 대상 옵션으로 생각중)",
    options=keys,
    default=prev,
    format_func=lambda k: label_by_key.get(k, k),
    key=ms_key
)

query = st.text_input(
    "검색어(자연어) 입력",
    placeholder="예: 인사팀 회의록 최근 3개월 pdf",
    key="query_text",
)

colA, colB, _ = st.columns([1, 1, 6])

# ✅ 검색 버튼: 상태 플래그만 올림
if colA.button("검색", type="primary", use_container_width=True):
    if not st.session_state.query_text.strip():
        st.warning("검색어를 입력해줘.")
        st.stop()
    st.session_state.should_search = True
    st.session_state.page = 1  # 새 검색은 1페이지부터

# ✅ 초기화
if colB.button("초기화", use_container_width=True):
    st.session_state.clear()
    st.rerun()

# ✅ 검색 실행: should_search가 True면 실행
if st.session_state.get("should_search", False):
    dsl = build_dsl(
        q=st.session_state.query_text.strip(),
        page=int(st.session_state.page),
        size=int(st.session_state.size),
        sort=sort,
        extension=extension,
        created_from=created_from,
        created_to=created_to,
        modified_from=modified_from,
        modified_to=modified_to,
    )

    with st.expander("전송 DSL 보기", expanded=False):
        st.code(json.dumps(dsl, ensure_ascii=False, indent=2), language="json")

    try:
        with st.spinner("Elasticsearch 검색 중..."):
            total, hits = es_search(dsl)

        st.success(f"총 {total}건")
        if not hits:
            st.info("검색 결과가 없습니다.")
        else:
            for h in hits:
                with st.container(border=True):
                    top = st.columns([5, 2, 2, 1])
                    title = h.title or "(제목 없음)"
                    top[0].markdown(f"### {title}")
                    top[1].markdown(f"**확장자**: `{h.extension or '-'}`")
                    top[2].markdown(f"**크기**: `{h.filesize_bytes:,} bytes`")
                    top[3].markdown(f"**score**: `{h.score:.2f}`")

                    meta = st.columns([4, 6])
                    meta[0].markdown(f"**파일명**: `{h.filename}`")
                    meta[0].markdown(f"**created**: `{h.created_at}`")
                    meta[0].markdown(f"**modified**: `{h.modified_at}`")

                    meta[1].markdown(f"**path_virtual**: `{h.path_virtual}`")
                    meta[1].markdown(f"**path_real**: `{h.path_real}`")

                    snippets: List[str] = []
                    if "body" in h.highlights:
                        snippets = h.highlights["body"]
                    elif "title" in h.highlights:
                        snippets = h.highlights["title"]

                    if snippets:
                        st.markdown("**본문 스니펫(하이라이트)**")
                        for s in snippets:
                            st.markdown(f"- {s}", unsafe_allow_html=True)
                    else:
                        st.caption("하이라이트가 없으면 analyzer/쿼리 조건에 따라 발생할 수 있어.")

        # ✅ pagination: try 안 / 렌더링 끝난 뒤
        new_page = render_pagination(
            total=total,
            page=int(st.session_state.page),
            size=int(st.session_state.size),
        )
        if new_page != int(st.session_state.page):
            st.session_state.page = new_page
            st.session_state.should_search = True
            st.rerun()

    except requests.exceptions.SSLError as e:
        st.error("SSL 오류: ES가 self-signed HTTPS일 가능성이 큼")
        st.code(str(e))
        st.info("실습이면 ES_VERIFY_SSL=false 로 두거나, 신뢰할 수 있는 CA/인증서로 교체해야 함.")
    except requests.HTTPError as e:
        st.error("ES 요청이 실패했어 (HTTPError)")
        st.code(str(e))
        try:
            st.json(e.response.json())
        except Exception:
            st.text(e.response.text if e.response is not None else "")
    except Exception as e:
        st.error("알 수 없는 오류")
        st.code(str(e))

st.divider()
st.caption(
    "운영으로 갈 땐 ES 계정을 Streamlit에 박아두기보다, "
    "별도 인증/권한 레이어(게이트웨이)를 두는 게 안전해."
)