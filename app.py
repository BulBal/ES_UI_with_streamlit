import json
import math
from datetime import date
from typing import List, Optional

import streamlit as st
import requests

from core.config import load_config
from core.es_client import EsClient
from dsl.base import SearchParams
from dsl.registry import DslRegistry

cfg = load_config()
es = EsClient(cfg)
dsl_registry = DslRegistry()

SEARCH_FIELD_OPTIONS = [
    ("title", "제목"),
    ("filename", "파일명"),
    ("path", "경로(하위 포함)"),
    ("author", "작성자"),
    ("keywords", "키워드"),
]

def render_pagination(total: int, page: int, size: int, window: int = 7) -> int:
    if total <= 0:
        return page
    total_pages = max(1, math.ceil(total / size))
    page = max(1, min(page, total_pages))
    c1, c2, c3 = st.columns([1, 6, 1])
    with c1:
        if st.button("◀ 이전", disabled=(page <= 1), use_container_width=True, key="pg_prev"):
            return page - 1
    with c2:
        half = window // 2
        start = max(1, page - half)
        end = min(total_pages, start + window - 1)
        start = max(1, end - window + 1)
        cols = st.columns(min(window + 4, 30))
        i = 0
        def page_btn(p: int, label: str = None):
            nonlocal i
            label = label or str(p)
            is_current = (p == page)
            if cols[i].button(label, disabled=is_current, use_container_width=True, key=f"pg_{p}"):
                return p
            i += 1
            return None
        newp = None
        if start > 1:
            newp = page_btn(1, "1")
            if newp: return newp
            if start > 2:
                cols[i].markdown("<div style='text-align:center; padding-top:8px;'>…</div>", unsafe_allow_html=True)
                i += 1
        for p in range(start, end + 1):
            newp = page_btn(p)
            if newp: return newp
        if end < total_pages:
            if end < total_pages - 1:
                cols[i].markdown("<div style='text-align:center; padding-top:8px;'>…</div>", unsafe_allow_html=True)
                i += 1
            newp = page_btn(total_pages, str(total_pages))
            if newp: return newp
        st.caption(f"{page} / {total_pages} 페이지 · 총 {total:,}건")
    with c3:
        if st.button("다음 ▶", disabled=(page >= total_pages), use_container_width=True, key="pg_next"):
            return page + 1
    return page

@st.cache_data(ttl=30)
def fetch_accessible_indices() -> List[str]:
    return es.list_indices()

st.set_page_config(page_title="ES Search (Streamlit)", layout="wide")
st.title("문서 검색 (Streamlit)")
st.caption("UI는 app.py에, DSL/ES 호출은 모듈로 분리")

with st.sidebar:
    st.subheader("검색 가능한 인덱스")
    try:
        idx_list = fetch_accessible_indices()
        st.caption(f"총 {len(idx_list)}개")
        selected_index = st.selectbox(
            "검색 인덱스",
            options=idx_list if idx_list else [cfg.es_default_index],
            index=0,
        )
    except Exception as e:
        st.warning("인덱스 목록 조회 실패")
        st.code(str(e))
        selected_index = cfg.es_default_index

    st.divider()
    st.subheader("검색 옵션")
    if "size" not in st.session_state:
        st.session_state.size = cfg.default_size
    if "page" not in st.session_state:
        st.session_state.page = 1

    size = st.number_input("페이지 크기", 1, 50, int(st.session_state.size), 1)
    if int(size) != int(st.session_state.size):
        st.session_state.size = int(size)
        st.session_state.page = 1

    page = st.number_input("페이지", 1, value=int(st.session_state.page), step=1)
    st.session_state.page = int(page)

    sort = st.selectbox("정렬", ["RELEVANCE", "RECENCY"], 0)

    st.divider()
    extension = st.text_input("확장자", placeholder="pdf / docx / pptx ...").strip() or None

    st.subheader("생성일 필터")
    c1, c2 = st.columns(2)
    created_from = c1.date_input("created_from", value=None)
    created_to = c2.date_input("created_to", value=None)
    if not st.checkbox("생성일 필터 사용", value=False):
        created_from = None
        created_to = None

    st.subheader("수정일 필터")
    m1, m2 = st.columns(2)
    modified_from = m1.date_input("modified_from", value=None)
    modified_to = m2.date_input("modified_to", value=None)
    if not st.checkbox("수정일 필터 사용", value=False):
        modified_from = None
        modified_to = None


# 검색 대상 필드 기능 
label_by_key = dict(SEARCH_FIELD_OPTIONS)
keys = list(label_by_key.keys())
ms_key = "ui_selected_fields"
prev = st.session_state.get(ms_key, ["title", "filename", "path"])
prev = [x for x in prev if x in keys] or ["title", "filename", "path"]

selected_fields = st.multiselect(
    "검색 대상 필드",
    options=keys,
    default=prev,
    format_func=lambda k: label_by_key.get(k, k),
    key=ms_key
)

# 검색창 기능 
query = st.text_input("검색어(자연어) 입력", placeholder="예: 인사팀 회의록 최근 3개월 pdf", key="query_text")
colA, colB, _ = st.columns([1, 1, 6])

if colA.button("검색", type="primary", use_container_width=True):
    if not st.session_state.query_text.strip():
        st.warning("검색어를 입력해줘.")
        st.stop()
    st.session_state.should_search = True
    st.session_state.page = 1

if colB.button("초기화", use_container_width=True):
    st.session_state.clear()
    st.rerun()

if st.session_state.get("should_search", False):
    builder = dsl_registry.get(selected_index)
    params = SearchParams(
        q=st.session_state.query_text.strip(),
        page=int(st.session_state.page),
        size=int(st.session_state.size),
        sort=sort,
        extension=extension,
        created_from=created_from,
        created_to=created_to,
        modified_from=modified_from,
        modified_to=modified_to,
        selected_fields=selected_fields
    )
    dsl = builder.build(params)

    with st.expander("전송 DSL 보기", expanded=False):
        st.code(json.dumps(dsl, ensure_ascii=False, indent=2), language="json")

    try:
        with st.spinner("Elasticsearch 검색 중..."):
            total, hits = es.search(selected_index, dsl)

        st.success(f"총 {total}건")
        if not hits:
            st.info("검색 결과가 없습니다.")
        else:
            ### 검색 결과 UI 설계
            for h in hits:
                with st.container(border=True):
                    top = st.columns([5, 2, 2, 1])
                    top[0].markdown(f"### {h.filename or '(제목 없음)'}")
                    top[1].markdown(f"**확장자**: `{h.extension or '-'}`")
                    top[2].markdown(f"**크기**: `{h.filesize_bytes:,} bytes`")
                    top[3].markdown(f"**score**: `{h.score:.2f}`")

                    meta = st.columns([4, 6])
                    meta[0].markdown(f"**파일명**: `{h.filename}`")
                    meta[0].markdown(f"**created**: `{h.created_at}`")
                    meta[0].markdown(f"**modified**: `{h.modified_at}`")
                    meta[1].markdown(f"**path_virtual**: `{h.path_virtual}`")
                    meta[1].markdown(f"**path_real**: `{h.path_real}`")

                    snippets: List[str] = (
                        h.highlights.get("title")
                        or h.highlights.get("filename")
                        or h.highlights.get("body")
                        or []
                    )
                    if snippets:
                        st.markdown("**하이라이트**")
                        for s in snippets:
                            st.markdown(f"- {s}", unsafe_allow_html=True)
                    else:
                        st.caption("하이라이트가 없으면 analyzer/쿼리 조건에 따라 발생할 수 있어.")

        new_page = render_pagination(total, int(st.session_state.page), int(st.session_state.size))
        if new_page != int(st.session_state.page):
            st.session_state.page = new_page
            st.session_state.should_search = True
            st.rerun()

    except requests.exceptions.SSLError as e:
        st.error("SSL 오류: self-signed 가능성")
        st.code(str(e))
    except requests.HTTPError as e:
        st.error("ES 요청 실패 (HTTPError)")
        st.code(str(e))
        try:
            st.json(e.response.json())
        except Exception:
            st.text(e.response.text if e.response is not None else "")
    except Exception as e:
        st.error("알 수 없는 오류")
        st.code(str(e))
