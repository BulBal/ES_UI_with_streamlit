import json
import math
from datetime import date
from typing import List, Optional

import streamlit as st
import requests
import pandas as pd
import re
import datetime as dt
import traceback

from st_aggrid import AgGrid, GridOptionsBuilder, JsCode

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
if "selected_path_display" not in st.session_state:
    st.session_state.selected_path_display = ""

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

# 검색창 기능 (음성 입력 기능 포함)
# 텍스트 입력과 음성 입력 버튼을 한 줄에 배치한다.
search_cols = st.columns([10, 1])
with search_cols[0]:
    st.text_input("검색어(자연어) 입력", placeholder="예: PDX 성능 테스트 ", key="query_text")
with search_cols[1]:
    st.markdown(
        """
        <div style="display:flex; align-items:end; height:68px;">
            <button id="voice-search-btn" type="button" title="음성 입력"
                style="
                    width:44px;
                    height:44px;
                    border:none;
                    border-radius:10px;
                    cursor:pointer;
                    font-size:20px;
                    background:#e5e7eb;
                    color:#111827;
                ">
                🎤
            </button>
        </div>
        """,
        unsafe_allow_html=True
    )

components.html(
    """
    <script>
    (() => {
      "use strict";

      const GLOBAL_KEY = "__STREAMLIT_SIMPLE_VOICE__";
      const app = window[GLOBAL_KEY] = window[GLOBAL_KEY] || {};

      const SpeechRecognition =
        window.parent.SpeechRecognition ||
        window.parent.webkitSpeechRecognition ||
        window.SpeechRecognition ||
        window.webkitSpeechRecognition;

      if (!SpeechRecognition) {
        console.warn("SpeechRecognition not supported");
        return;
      }

      function getHostDocument() {
        try {
          if (window.parent && window.parent.document) {
            void window.parent.document.body;
            return window.parent.document;
          }
        } catch (e) {}
        return document;
      }

      const hostDoc = getHostDocument();

      function findButton() {
        return hostDoc.getElementById("voice-search-btn");
      }

      function findInput() {
        return (
          hostDoc.querySelector('input[aria-label="검색어(자연어) 입력"]') ||
          hostDoc.querySelector('input[placeholder="예: PDX 성능 테스트 "]')
        );
      }

      function setInputValue(input, value) {
        if (!input) return false;

        const proto = window.parent.HTMLInputElement?.prototype || HTMLInputElement.prototype;
        const desc = Object.getOwnPropertyDescriptor(proto, "value");
        const setter = desc && desc.set;

        if (setter) setter.call(input, value);
        else input.value = value;

        input.dispatchEvent(new Event("input", { bubbles: true }));
        input.dispatchEvent(new Event("change", { bubbles: true }));
        return true;
      }

      function setButtonColor(isListening) {
        const btn = app.button;
        if (!btn) return;

        if (isListening) {
          btn.style.background = "#ef4444";
          btn.style.color = "#ffffff";
        } else {
          btn.style.background = "#e5e7eb";
          btn.style.color = "#111827";
        }
      }

      if (!app.recognition) {
        app.recognition = new SpeechRecognition();
        app.recognition.lang = "ko-KR";
        app.recognition.interimResults = false;
        app.recognition.continuous = false;
        app.recognition.maxAlternatives = 1;
      }

      app.isListening = app.isListening || false;

      const recognition = app.recognition;

      recognition.onstart = () => {
        app.isListening = true;
        setButtonColor(true);
      };

      recognition.onend = () => {
        app.isListening = false;
        setButtonColor(false);
      };

      recognition.onerror = () => {
        app.isListening = false;
        setButtonColor(false);
      };

      recognition.onresult = (event) => {
        let finalText = "";

        for (let i = event.resultIndex; i < event.results.length; i++) {
          const result = event.results[i];
          if (result.isFinal) {
            finalText += result[0].transcript;
          }
        }

        finalText = finalText.trim();
        if (!finalText) return;

        const input = findInput();
        setInputValue(input, finalText);
      };

      function bind() {
        app.button = findButton();
        if (!app.button) return false;

        if (app.button.dataset.voiceBound === "1") {
          setButtonColor(app.isListening);
          return true;
        }

        app.button.dataset.voiceBound = "1";
        setButtonColor(false);

        app.button.addEventListener("click", async (e) => {
          e.preventDefault();

          if (app.isListening) {
            try {
              recognition.stop();
            } catch (err) {
              console.warn(err);
            }
            return;
          }

          try {
            await navigator.mediaDevices.getUserMedia({ audio: true }).then(stream => {
              stream.getTracks().forEach(track => track.stop());
            });
            recognition.start();
          } catch (err) {
            console.warn("microphone permission error:", err);
            app.isListening = false;
            setButtonColor(false);
          }
        });

        return true;
      }

      if (!bind()) {
        const obs = new MutationObserver(() => {
          if (bind()) obs.disconnect();
        });
        obs.observe(hostDoc.documentElement, { childList: true, subtree: true });
      }
    })();
    </script>
    """,
    height=0,
)
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

            # 복사 버튼 렌더러에서 사용할 값
            display_df["copy"] = display_df["path_real"].fillna("").astype(str)

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

            copy_cell_renderer = JsCode("""
            class CopyButtonRenderer {
                init(params) {
                    this.params = params;
                    this.eGui = document.createElement('div');
                    this.eGui.style.display = 'flex';
                    this.eGui.style.justifyContent = 'center';
                    this.eGui.style.alignItems = 'center';
                    this.eGui.style.height = '100%';

                    const button = document.createElement('button');
                    button.innerText = '📋';
                    button.title = params.value || '경로 없음';
                    button.style.cursor = 'pointer';
                    button.style.border = '1px solid #d1d5db';
                    button.style.background = '#ffffff';
                    button.style.borderRadius = '6px';
                    button.style.padding = '2px 6px';
                    button.style.fontSize = '14px';
                    button.style.lineHeight = '1.2';

                    if (!params.value) {
                        button.disabled = true;
                        button.style.cursor = 'not-allowed';
                        button.style.opacity = '0.5';
                    }

                    button.addEventListener('click', async (e) => {
                        e.stopPropagation();
                        if (!params.value) return;

                        try {
                            await navigator.clipboard.writeText(params.value);
                            button.innerText = '✅';
                            setTimeout(() => {
                                button.innerText = '📋';
                            }, 900);
                        } catch (err) {
                            console.error('clipboard copy failed', err);
                            button.innerText = '❌';
                            setTimeout(() => {
                                button.innerText = '📋';
                            }, 900);
                        }
                    });

                    this.eGui.appendChild(button);
                }

                getGui() {
                    return this.eGui;
                }
            }
            """)

            gb = GridOptionsBuilder.from_dataframe(display_df)

            gb.configure_default_column(
                resizable=True,
                sortable=False,
                filter=False,
                wrapText=False,
                autoHeight=False,
            )

            gb.configure_column(
                "copy",
                header_name="경로 복사",
                width=80,
                pinned="left",
                cellRenderer=copy_cell_renderer,
                sortable=False,
                filter=False,
                suppressMenu=True,
            )

            gb.configure_column("filename", header_name="파일명", width=filename_width)
            gb.configure_column("path_real", header_name="파일 경로", width=path_width)
            gb.configure_column("extension", header_name="확장자", width=100)
            gb.configure_column("filesize", header_name="파일 크기", width=110)
            gb.configure_column(
                "created_at",
                header_name="생성일",
                width=160,
                valueFormatter="value ? new Date(value).toLocaleString('sv-SE').slice(0,16).replace('T',' ') : ''",
            )
            gb.configure_column(
                "modified_at",
                header_name="수정일",
                width=160,
                valueFormatter="value ? new Date(value).toLocaleString('sv-SE').slice(0,16).replace('T',' ') : ''",
            )

            gb.configure_selection(
                selection_mode="single",
                use_checkbox=False,
            )

            grid_options = gb.build()
            grid_options["rowHeight"] = 35
            grid_options["headerHeight"] = 40
            grid_options["suppressRowClickSelection"] = False
            grid_options["rowSelection"] = "single"
            grid_options["domLayout"] = "normal"

            grid_response = AgGrid(
                display_df,
                gridOptions=grid_options,
                height=table_height,
                width="100%",
                allow_unsafe_jscode=True,
                enable_enterprise_modules=False,
                fit_columns_on_grid_load=False,
                update_mode="SELECTION_CHANGED",
                reload_data=False,
                theme="streamlit",
                key=f"aggrid_result_{st.session_state.page}",
            )

            selected_rows = grid_response.get("selected_rows", [])

            if isinstance(selected_rows, pd.DataFrame):
                selected_rows = selected_rows.to_dict("records")

            if selected_rows:
                selected_path = str(selected_rows[0].get("path_real", "") or "")
                st.session_state.selected_path_display = selected_path
            else:
                st.session_state.selected_path_display = ""

            if st.session_state.selected_path_display:
                st.markdown("#### 선택한 파일 경로")
                st.text_input(
                    "전체 경로",
                    key="selected_path_display",
                    label_visibility="collapsed",
                )

            # 마지막으로 복사한 경로 표시
            if st.session_state.selected_path_display:
                st.markdown("#### 선택한 파일 경로")
                st.text_input(
                    "전체 경로",
                    key="selected_path_display",
                    label_visibility="collapsed",
                )

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

