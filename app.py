import json
import math
from datetime import date
from typing import List, Optional

import streamlit as st
import pyperclip
import requests
import pandas as pd
import re
import datetime as dt
import traceback

from core.config import load_config
from core.es_client import EsClient
from dsl.base import SearchParams
from dsl.registry import DslRegistry
from core.df_builder import hits_to_rows, rows_to_df

cfg = load_config()
es = EsClient(cfg)
dsl_registry = DslRegistry()

SEARCH_TARGET_OPTIONS = [
    ("ALL", "전체"),
    ("FILE_ONLY", "파일만"),
    ("DIR_ONLY", "폴더만"),
]

label_by_target = dict(SEARCH_TARGET_OPTIONS)
target_keys = list(label_by_target.keys())

EXTENSION_HELP = """
### 지원 확장자 목록

**1. 이미지**
- `*.jpg`
- `*.jpeg`
- `*.png`
- `*.gif`
- `*.bmp`
- `*.tif`
- `*.tiff`
- `*.webp`
- `*.svg`
- `*.heic`
- `*.ai`
- `*.ico`
- `*.psd`

**2. 문서**
- `*.pdf`
- `*.txt`
- `*.md`
- `*.rtf`
- `*.doc`
- `*.docx`
- `*.ppt`
- `*.pptx`
- `*.xls`
- `*.xlsx`
- `*.xlsm`
- `*.csv`
- `*.hwp`
- `*.hwpx`

**3. 설정 / 구성**
- `*.conf`
- `*.properties`
- `*.policy`
- `*.manifest`
- `*.yml`
- `*.yaml`
- `*.json`
- `*.xml`
- `*.toml`
- `*.env`

**4. 압축 / 패키징**
- `*.zip`
- `*.7z`
- `*.rar`
- `*.tar`
- `*.gz`
- `*.tgz`
- `*.bz2`
- `*.xz`
- `*.iso`
- `*.cab`

**5. 기타**
- `*.old`
"""

# bytes 단위 표현용 함수
def human_readable_size(num_bytes: int | float | None) -> str:
    if num_bytes is None:
        return ""
    try:
        size = float(num_bytes)
    except (TypeError, ValueError):
        return ""

    units = ["B", "KB", "MB", "GB", "TB", "PB"]
    for unit in units:
        if size < 1024 or unit == units[-1]:
            if unit == "B":
                return f"{int(size)} {unit}"
            return f"{size:.1f} {unit}"
        size /= 1024

    return ""

# 검색모드
def normalize_search_params() -> tuple[str, list[str] | None]:
    target_mode = st.session_state.target_mode
    raw_extension = st.session_state.get("raw_extension", "")

    if target_mode == "DIR_ONLY":
        return target_mode, None

    return target_mode, parse_extensions(raw_extension)

# 확장자 입력창 파싱 함수
def parse_extensions(ext_str: str) -> list[str]:
    if not ext_str:
        return []

    parts = re.split(r"[,\s;/|]+", ext_str.lower())
    cleaned = []

    for p in parts:
        p = p.strip().lstrip(".")
        if p:
            cleaned.append(p)

    # 중복 제거 + 입력 순서 유지
    return list(dict.fromkeys(cleaned))

def apply_ui_sort(df: pd.DataFrame, sort_col: str, ascending: bool) -> pd.DataFrame:
    """
    UI 단 정렬: ES 정렬과 완전히 분리.
    """
    if df.empty or sort_col not in df.columns:
        return df
    # ✅ NaN/빈값 섞여도 안정적으로 정렬되게
    return df.sort_values(by=sort_col, ascending=ascending, na_position="last").reset_index(drop=True)

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
        cols = st.columns(min(window + 5, 30))
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

# -----------------------------
# 기본 session 초기화
# -----------------------------
if "size" not in st.session_state:
    st.session_state.size = cfg.default_size
if "page" not in st.session_state:
    st.session_state.page = 1
if "query_text" not in st.session_state:
    st.session_state.query_text = ""
if "target_mode" not in st.session_state:
    st.session_state.target_mode = "ALL"
if "raw_extension" not in st.session_state:
    st.session_state.raw_extension = ""

# -----------------------------
# 검색 대상 / 검색창 (본문 상단)
# -----------------------------
target_mode = st.radio(
    "검색 대상 필드 선택",
    options=target_keys,
    index = 0,
    format_func=lambda k: label_by_target.get(k, k),
    horizontal=True,
    key="target_mode",
)

# 검색창 기능 
st.text_input("검색어(자연어) 입력", placeholder="예: PDX 성능 테스트 ", key="query_text")
colA, colB, _ = st.columns([1, 1, 6])

if colA.button("검색", type="primary", use_container_width=True):
    if not st.session_state.query_text.strip():
        st.warning("검색어를 입력해줘.")
        st.stop()
    st.session_state.should_search = True
    st.session_state.page = 1
def reset_search_state(keep_keys: None):
    keep_keys = keep_keys or []
    keep ={k: st.session_state.get(k) for k in keep_keys if k in st.session_state}
    st.session_state.clear()
    for k, v in keep.items():
        st.session_state[k] = v

if colB.button("초기화", use_container_width=True):
    reset_search_state(keep_keys=["selected_index", "size", "target_mode"])
    st.rerun()

with st.sidebar:
    st.subheader("검색 가능한 인덱스")

    IDX_KEY = "selected_index"
    try:
        idx_list = fetch_accessible_indices()
        st.caption(f"총 {len(idx_list)}개")

        index_options = idx_list if idx_list else [cfg.es_default_index]
        # 이전 선택 복구(목록에 없으면 기본값)
        prev_selected = st.session_state.get(IDX_KEY, cfg.es_default_index)
        if prev_selected not in index_options:
            prev_selected = index_options[0]

        selected_index = st.selectbox(
            "검색 인덱스",
            options=index_options,
            index= index_options.index(prev_selected) if prev_selected in index_options else 0,
            key=IDX_KEY
        )
    except Exception as e:
        st.warning("인덱스 목록 조회 실패")
        st.code(str(e))
        # 인덱스 선택 UI는 기본값 하나로 fallback
        if IDX_KEY in st.session_state:
            st.session_state[IDX_KEY] = cfg.es_default_index
        selected_index = st.session_state.get(IDX_KEY, cfg.es_default_index)

    st.divider()

    sort = st.selectbox("정렬", ["RELEVANCE(유사도 우선)"], 0)

    st.divider()

    raw_extension = st.text_input(
        "확장자",
        placeholder="pdf, docx, pptx ...",
        key="raw_extension",
        disabled=(target_mode == "DIR_ONLY"),
        help="폴더만 검색에서는 확장자 필터를 사용하지 않습니다." if target_mode == "DIR_ONLY" else EXTENSION_HELP,
    )
    
    extension = None if target_mode == "DIR_ONLY" else parse_extensions(raw_extension)

    if not st.checkbox("확장자 필터 사용", value=False):
        extension = None
       

    MIN_DATE = dt.date(1990,1,1)
    MAX_DATE = dt.date.today()

    st.subheader("생성일 필터")
    c1, c2 = st.columns(2)
    created_from = c1.date_input("created_from", value=None, min_value= MIN_DATE, max_value = MAX_DATE)
    created_to = c2.date_input("created_to", value=None, min_value= MIN_DATE, max_value = MAX_DATE)
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




if st.session_state.get("should_search", False):
    selected_index = st.session_state.get(IDX_KEY, cfg.es_default_index)

    builder = dsl_registry.get(selected_index)
    params = SearchParams(
        q=st.session_state.query_text.strip(),
        page=int(st.session_state.page),
        size=int(st.session_state.size),
        sort=sort,
        extension=extension,
        target_mode=target_mode,
        created_from=created_from,
        created_to=created_to,
        modified_from=modified_from,
        modified_to=modified_to,
    )
    dsl = builder.build(params)

    with st.expander("전송 DSL 보기", expanded=False):
        st.code(json.dumps(dsl, ensure_ascii=False, indent=2), language="json")

    test_mode = False

    try:
        with st.spinner("Elasticsearch 검색 중..."):
            total, hits = es.search(selected_index, dsl)
            rows = []
            if test_mode:
                for h in hits :
                    rows.append({
                        "filename": h.filename,
                        "extension": h.extension,
                        "created_at": h.created_at,
                        "modified_at": h.modified_at,
                        "filesize_bytes": h.filesize_bytes,
                        "path_virtual": h.path_virtual,
                        "id": h.id,
                    })
            else:
                for h in hits:
                    rows.append({
                        "filename": h.filename,
                        "extension": h.extension,
                        "created_at": h.created_at,
                        "modified_at": h.modified_at,
                        "filesize_bytes": h.filesize_bytes,
                        "path_virtual": h.path_virtual,
                        "path_real" : h.path_real,
                    })

            result_df = pd.DataFrame(rows)
            if not rows:
                pass
            else:
                for col in ["created_at", "modified_at"]:
                    if col in result_df.columns:
                        result_df[col] = pd.to_datetime(result_df[col], errors="coerce").dt.floor("min")
                    else:
                        result_df[col] = pd.NaT
                if "filesize_bytes" in result_df.columns:
                    result_df["filesize"] = result_df["filesize_bytes"].apply(human_readable_size)
                else:
                    result_df["filesize_bytes"] = pd.NA
                    result_df['filesize']= ""
            
        st.success(f"총 {total}건")
        if not hits:
            st.info("검색 결과가 없습니다.")
        else:
            display_df = result_df[[
                "filename",
                "path_real",
                "extension",
                "filesize",
                "created_at",
                "modified_at",
            ]].copy()

            # ✅ CSV처럼 보이게: 전체 폭 + 스크롤
            # 테이블 행의 높이를 조절하기 위한 변수
            max_filename_len = (
                display_df["filename"].fillna("").astype(str).map(len).max()
                if not display_df.empty else 10
            )
            max_path_len = (
                display_df["path_real"].fillna("").astype(str).map(len).max()
                if not display_df.empty else 20
            )

            filename_width = int(min(max(180, max_filename_len * 9), 500))
            path_width = int(min(max(400, max_path_len * 7), 1200))
            table_height = min(900, 80 + len(display_df) * 35)

            # 선택된 경로 표시 영역 초기화
            if "selected_path_display" not in st.session_state:
                st.session_state.selected_path_display = ""

            # -----------------------------------------
            # 왼쪽: 행별 복사 버튼 / 오른쪽: 결과 테이블
            # -----------------------------------------
            copy_col, table_col = st.columns([1, 12], vertical_alignment="top")

            with copy_col:
                st.markdown("##### 복사")
                st.write("")  # 헤더 높이 보정

                for idx, row in display_df.reset_index(drop=True).iterrows():
                    path_value = str(row.get("path_real", "") or "")

                    if st.button(
                        "📋",
                        key=f"copy_path_row_{st.session_state.page}_{idx}",
                        use_container_width=True,
                        disabled=(not path_value),
                        help=path_value if path_value else "경로 없음",
                    ):
                        try:
                            pyperclip.copy(path_value)
                            st.session_state.selected_path_display = path_value
                            st.toast("경로를 클립보드에 복사했습니다.")
                        except Exception as e:
                            st.error(f"복사 실패 : {e}")

            with table_col:
                st.dataframe(
                    display_df,
                    use_container_width=True,
                    hide_index=True,
                    height=table_height,
                    key="result_table",
                    column_config={
                        "filename": st.column_config.Column(
                            "파일명",
                            width=filename_width,
                        ),
                        "created_at": st.column_config.DatetimeColumn(
                            "생성일",
                            format="YYYY-MM-DD HH:mm",
                            width=160,
                        ),
                        "modified_at": st.column_config.DatetimeColumn(
                            "수정일",
                            format="YYYY-MM-DD HH:mm",
                            width=160,
                        ),
                        "extension": st.column_config.Column(
                            "확장자",
                            width="small",
                        ),
                        "filesize": st.column_config.Column(
                            "파일 크기",
                            width="small",
                        ),
                        "path_real": st.column_config.Column(
                            "파일 경로",
                            width=path_width,
                        ),
                    },
                )

            # 마지막으로 복사한 경로 표시
            if st.session_state.selected_path_display:
                st.markdown("#### 마지막으로 복사한 파일 경로")

                c1, c2 = st.columns([8, 1])

                with c1:
                    st.text_input(
                        "전체 경로",
                        key="selected_path_display",
                        label_visibility="collapsed",
                    )

                with c2:
                    if st.button("한번 더 복사", key="copy_selected_path_again"):
                        try:
                            pyperclip.copy(st.session_state.selected_path_display)
                            st.success("경로를 다시 클립보드에 복사했습니다")
                        except Exception as e:
                            st.error(f"복사 실패 : {e}")

            new_page = render_pagination(
                total=total,
                page=int(st.session_state.page),
                size=int(st.session_state.size),
                window=7,
            )

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
        # 오류 트레이싱 용
        #st.code(traceback.format_exc())

