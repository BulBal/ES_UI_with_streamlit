import json
import math
from datetime import date
from typing import List, Optional

import streamlit as st
import streamlit.components.v1 as components
import streamlit.components.v2 as components_v2
from typing import Optional, Iterable
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


RESULT_SNAPSHOT_SIZE = 3000  # 1차 검색 결과를 최대 몇 건까지 로컬 결과 집합으로 들고 있을지

def apply_refine_filter(df: pd.DataFrame, query: str) -> pd.DataFrame:
    if df is None:
        return pd.DataFrame()
    if df.empty:
        return df.copy()

    query = (query or "").strip().lower()
    if not query:
        return df.copy()

    tokens = [t for t in re.split(r"\s+", query) if t]
    if not tokens:
        return df.copy()

    searchable_cols = ["filename", "path_real", "extension"]
    lower_map = {
        col: df[col].fillna("").astype(str).str.lower()
        for col in searchable_cols
        if col in df.columns
    }

    mask = pd.Series(True, index=df.index)

    for token in tokens:
        token_mask = pd.Series(False, index=df.index)
        for _, series in lower_map.items():
            token_mask = token_mask | series.str.contains(token, regex=False)
        mask = mask & token_mask

    return df.loc[mask].copy()


def paginate_local_df(df: pd.DataFrame, page: int, size: int) -> tuple[pd.DataFrame, int, int]:
    if df is None:
        return pd.DataFrame(), 1, 0

    total = len(df)
    if total == 0:
        return df.copy(), 1, 0

    total_pages = max(1, math.ceil(total / size))
    page = max(1, min(page, total_pages))

    start = (page - 1) * size
    end = start + size
    return df.iloc[start:end].copy(), page, total


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

def diagnose_voice_status(status: str, error: str, pack_status: str, install_result: any) -> dict:
    """
    음성입력 상태를 진단하고 사용자에게 표시할 메시지를 반환합니다.
    
    Returns:
        {
            'severity': 'info' | 'warning' | 'error',
            'title': str,
            'message': str,
            'action_needed': bool,
            'display': bool
        }
    """
    if status == 'listening':
        return {
            'severity': 'info',
            'title': '🎙️ 음성인식 중',
            'message': '말하는 내용을 인식하는 중입니다. 버튼을 다시 누르면 중지됩니다.',
            'action_needed': False,
            'display': True,
        }

    if status == 'start-failed':
        return {
            'severity': 'warning',
            'title': '⚠️ Chrome Flags 설정 필요',
            'message': 'Chrome 브라우저의 "Experimental Web Platform features" 플래그를 활성화해야 음성입력을 사용할 수 있습니다.\n\n아래 링크를 클릭하여 Chrome Flags 페이지로 이동하세요:',
            'chrome_flags_url': 'chrome://flags/#enable-experimental-web-platform-features',
            'action_needed': True,
            'display': True,
        }

    if error:
        error_messages = {
            'not-allowed': '마이크 접근 권한이 거부되었습니다. 브라우저 설정에서 음성인식 권한을 허용해주세요.',
            'no-speech': '음성이 감지되지 않았습니다. 마이크가 정상 작동하는지 확인해주세요.',
            'network-error': '네트워크 오류가 발생했습니다. 인터넷 연결을 확인해주세요.',
            'service-not-allowed': '음성인식 서비스가 허용되지 않았습니다.',
        }
        error_msg = error_messages.get(error, f'오류 발생: {error}')
        return {
            'severity': 'error',
            'title': '❌ 음성입력 오류',
            'message': error_msg,
            'action_needed': True,
            'display': True,
        }

    if pack_status in ['downloading', 'installing']:
        return {
            'severity': 'info',
            'title': '⏳ 음성 리소스 설치 중',
            'message': '한국어 음성 팩을 처음 다운로드하는 중입니다. 잠시 기다려주세요.',
            'action_needed': False,
            'display': True,
        }

    if install_result is False:
        return {
            'severity': 'error',
            'title': '❌ 음성 팩 설치 실패',
            'message': '음성 팩을 설치할 수 없습니다. 브라우저를 다시 시작하거나 Chrome을 최신 버전으로 업데이트해주세요.',
            'action_needed': True,
            'display': True,
        }

    # 정상 상태 또는 기본 대기 상태는 별도 알림 표시 없이 숨김.
    return {
        'severity': 'info',
        'title': '🎤 음성입력 준비 완료',
        'message': '마이크 버튼을 눌러 음성으로 검색어를 입력할 수 있습니다.',
        'action_needed': False,
        'display': False,
    }

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
# if "query_text" not in st.session_state:
#     st.session_state["query_text"] = ""
if "result_snapshot_df" not in st.session_state:
    st.session_state["result_snapshot_df"] = None
if "working_result_df" not in st.session_state:
    st.session_state["working_result_df"] = None
if "result_total" not in st.session_state:
    st.session_state["result_total"] = 0
if "local_page" not in st.session_state:
    st.session_state["local_page"] = 1
if "refine_query" not in st.session_state:
    st.session_state["refine_query"] = ""
if "pending_transcript" not in st.session_state:
    st.session_state["pending_transcript"] = None
if "should_search" not in st.session_state:
    st.session_state["should_search"] = False
if "last_applied_transcript" not in st.session_state:
    st.session_state["last_applied_transcript"] = None
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

pending = st.session_state.get("pending_transcript")

if pending is not None:
    st.session_state["query_text"] = pending
    st.session_state["pending_transcript"] = None
    st.session_state["should_search"] = False

# ===== 음성 입력 도움말 팝업 =====
if "show_flags_help" not in st.session_state:
    st.session_state["show_flags_help"] = False

help_col, _, _ = st.columns([2, 8, 2])
with help_col:
    if st.button("🔧 음성입력 설정 도움말", use_container_width=True):
        st.session_state["show_flags_help"] = not st.session_state["show_flags_help"]

if st.session_state.get("show_flags_help", False):
    st.info("""
**Chrome Flags 설정 (처음 한 번만)**

1. [chrome://flags/#enable-experimental-web-platform-features](chrome://flags/#enable-experimental-web-platform-features) 접속
2. "Experimental Web Platform features" → "Enabled" 선택
3. "Relaunch" 버튼으로 Chrome 재시작
    """)

# 검색창 기능 (음성 입력 기능 포함)
# 텍스트 입력과 음성 입력 버튼을 한 줄에 배치한다.
search_cols = st.columns([10, 1])
with search_cols[0]:
    st.text_input("검색어(자연어) 입력", placeholder="예: PDX 성능 테스트 ", key="query_text")
voice_component = st.components.v2.component(
        name="voice_search_v2_minimal",
        html="""
        <div class="voice-container">
          <button id="voice-button" type="button" title="음성 입력">🎤</button>
        </div>
        """,
        css="""
        .voice-container {
          display: flex;
          align-items: end;
          height: 68px;
        }

        #voice-button {
          width: 44px;
          height: 44px;
          border: none;
          border-radius: 10px;
          cursor: pointer;
          font-size: 20px;
          background: #e5e7eb;
          color: #111827;
        }

        #voice-button.listening {
          background: #ef4444;
          color: #ffffff;
        }
        """,
        js="""
        export default function(component) {
          const { parentElement, data, setStateValue } = component;
          const button = parentElement.querySelector('#voice-button');
          if (!button) return;


        // 객체 선언부가 가장 중요함 
        // SpeechRecognition API는 브라우저마다 지원 여부와 기능이 다르기 때문에, 글로벌 객체에 인스턴스를 하나 만들어서 재사용하는 방식을 택함.

          const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
          if (!SpeechRecognition) {
            setStateValue('status', 'unsupported');
            setStateValue('error', 'SpeechRecognition not supported');
            return;
          }

          const lang = (data && data.lang) || 'ko-KR';

          function findInput() {
            return (
              document.querySelector('input[aria-label="검색어(자연어) 입력"]') ||
              document.querySelector('input[placeholder="예: PDX 성능 테스트 "]')
            );
          }

          function setInputValue(input, value) {
            if (!input) return;
            const proto = window.HTMLInputElement.prototype;
            const descriptor = Object.getOwnPropertyDescriptor(proto, 'value');
            const setter = descriptor && descriptor.set;
            if (setter) setter.call(input, value);
            else input.value = value;
            input.dispatchEvent(new Event('input', { bubbles: true }));
            input.dispatchEvent(new Event('change', { bubbles: true }));
          }

          const globalKey = '__STREAMLIT_VOICE_SEARCH_V2_MIN__';
          const app = window[globalKey] = window[globalKey] || {};

          if (!app.recognition) {
            app.recognition = new SpeechRecognition();
            app.recognition.lang = lang;
            app.recognition.interimResults = false;
            app.recognition.continuous = false;
            app.recognition.maxAlternatives = 1;
            app.recognition.processLocally = true;
          }

          const recognition = app.recognition;
          let isListening = app.isListening || false;

          function renderButton() {
            button.classList.toggle('listening', isListening);
          }

          recognition.onstart = () => {
            isListening = true;
            app.isListening = true;
            renderButton();
            setStateValue('status', 'listening');
            setStateValue('error', null);
          };

          recognition.onend = () => {
            isListening = false;
            app.isListening = false;
            renderButton();
            setStateValue('status', 'idle');
          };

          recognition.onerror = (event) => {
            isListening = false;
            app.isListening = false;
            renderButton();
            setStateValue('status', 'error');
            setStateValue('error', event?.error || event?.message || 'unknown-error');
          };

          recognition.onresult = (event) => {
            let finalText = '';
            for (let i = event.resultIndex; i < event.results.length; i++) {
              const result = event.results[i];
              if (result.isFinal) finalText += result[0].transcript;
            }
            finalText = finalText.trim();
            if (!finalText) return;
            const input = findInput();
            setInputValue(input, finalText);
            setStateValue('status', 'done');
            setStateValue('last_transcript', finalText);
          };

          async function ensureLanguagePack() {
            try {
              if (typeof SpeechRecognition.available !== 'function') {
                setStateValue('pack_status', 'no-available-api');
                setStateValue('install_result', false);
                return true;
              }

              const status = await SpeechRecognition.available({
                langs: [lang],
                processLocally: true,
              });
              setStateValue('pack_status', status);

              if (status === 'available') return true;

              if (status === 'downloadable' || status === 'downloading') {
                if (typeof SpeechRecognition.install !== 'function') {
                  setStateValue('install_result', false);
                  return false;
                }
                const installed = await SpeechRecognition.install({ 
                langs: [lang],
                processLocally: true,
                });
                setStateValue('install_result', installed);
                return installed;
              }

              return false;
            } catch (err) {
              setStateValue('pack_status', 'check-failed');
              setStateValue('install_result', false);
              setStateValue('error', err?.message || String(err));
              return false;
            }
          }
          button.onmousedown = (e) => {
            e.preventDefault();
            e.stopPropagation();
            };

          button.onclick = async (e) => {
            e.preventDefault();
            e.stopPropagation();

            if (isListening) {
              try {
                recognition.stop();
              } catch (err) {
                setStateValue('error', err?.message || String(err));
              }
              return;
            }

            const ready = await ensureLanguagePack();
            if (!ready) {
              setStateValue('status', 'install-failed');
              alert('한국어 온디바이스 음성팩을 설치할 수 없습니다.');
              return;
            }

            try {
              await navigator.mediaDevices.getUserMedia({ audio: true }).then((stream) => {
                stream.getTracks().forEach((track) => track.stop());
              });
              recognition.processLocally = true;
              recognition.lang = lang;
              recognition.start();
            } catch (err) {
              setStateValue('status', 'start-failed');
              setStateValue('error', err?.message || String(err));
            }
          };

          renderButton();
        }
        """,
    )

with search_cols[1]:
    result = voice_component(
        key="voice_component_instance",
        data={"lang": "ko-KR"},
        default={
            "status": "idle",
            "error": None,
            "pack_status": None,
            "install_result": None,
            "last_transcript": None,
        },
        on_status_change=lambda: None,
        on_error_change=lambda: None,
        on_pack_status_change=lambda: None,
        on_install_result_change=lambda: None,
        on_last_transcript_change=lambda: None,
        width="content",
        height="content",
    )

# ===== 음성입력 상태 진단 및 안내 메시지 =====
voice_status = diagnose_voice_status(
    status=getattr(result, "status", "idle"),
    error=getattr(result, "error", None),
    pack_status=getattr(result, "pack_status", None),
    install_result=getattr(result, "install_result", None)
)

# 상태 메시지 표시
if voice_status.get('display', False):
    if voice_status['severity'] == 'error':
        st.error(f"**{voice_status['title']}**\n\n{voice_status['message']}")
    elif voice_status['severity'] == 'warning':
        if 'chrome_flags_url' in voice_status:
            st.warning(
                f"**{voice_status['title']}**\n\n"
                f"{voice_status['message']}\n\n"
                f"[🔗 Chrome Flags 페이지 열기]({voice_status['chrome_flags_url']})"
            )
        else:
            st.warning(f"**{voice_status['title']}**\n\n{voice_status['message']}")
    else:
        st.info(f"**{voice_status['title']}**\n\n{voice_status['message']}")

if getattr(result, "last_transcript", None):
    transcript = result.last_transcript.strip()
    if (
        transcript
        and transcript != st.session_state.get("last_applied_transcript")
    ):
        st.session_state["pending_transcript"] = transcript
        st.session_state["last_applied_transcript"] = transcript
        st.session_state["should_search"] = False
        st.rerun()

query = st.session_state.get("query_text", "").strip()

#필요할 때만 주석 해제
# st.write("status:", result.status)
# st.write("error:", result.error)
# st.write("pack_status:", result.pack_status)
# st.write("install_result:", result.install_result)
# st.write("last_transcript:", result.last_transcript)

colA, colB, _ = st.columns([1, 1, 6])

if colA.button("검색", type="primary", use_container_width=True):
    if not st.session_state.get("query_text", "").strip():
        st.warning("검색어를 입력해줘.")
        st.stop()

    st.session_state["should_search"] = True
    st.session_state["local_page"] = 1
def reset_search_state(keep_keys: Optional[Iterable[str]] = None):
    keep_keys = keep_keys or []
    keep = {k: st.session_state.get(k) for k in keep_keys if k in st.session_state}

    st.session_state.clear()

    for k, v in keep.items():
        st.session_state[k] = v

    st.session_state["query_text"] = ""
    st.session_state["pending_transcript"] = None
    st.session_state["should_search"] = False
    st.session_state["last_applied_transcript"] = None

    st.session_state["raw_extension"] = ""
    st.session_state["selected_path_display"] = ""

    st.session_state["result_snapshot_df"] = None
    st.session_state["working_result_df"] = None
    st.session_state["result_total"] = 0
    st.session_state["local_page"] = 1
    st.session_state["refine_query"] = ""

if colB.button("초기화", use_container_width=True):
    reset_search_state(keep_keys=["selected_index", "size", "target_mode"])
    st.rerun()

with st.sidebar:
    st.subheader("검색 가능한 인덱스")

    IDX_KEY = "selected_index"
    try:
        idx_list = fetch_accessible_indices()
        st.caption(f"총 {len(idx_list)}개")

        index_options = idx_list
        # 이전 선택 복구(목록에 없으면 기본값)
        if index_options:
            prev_selected = st.session_state.get(IDX_KEY)
            if prev_selected not in index_options:
                prev_selected = index_options[0]
            selected_index = st.selectbox(
                "검색 인덱스",
                options=index_options,
                index= index_options.index(prev_selected),
                key=IDX_KEY
            )
        else:
            st.session_state.pop(IDX_KEY, None)
            selected_index = None
            st.warning("No accessible index found.")
    
    except Exception as e:
        st.warning("인덱스 목록 조회 실패")
        st.code(str(e))
        # 인덱스 선택 UI는 기본값 하나로 fallback
        st.session_state.pop(IDX_KEY, None)
        selected_index = None

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
    selected_index = st.session_state.get(IDX_KEY)
    if not selected_index:
        st.session_state["should_search"] = False
        st.warning("No selectable index.")
        st.stop()
    st.session_state["should_search"] = False

    builder = dsl_registry.get(selected_index)

    # 1차 ES 검색은 snapshot 확보용으로 상위 N건 고정
    params = SearchParams(
        q=st.session_state.get("query_text", "").strip(),
        page=1,
        size=RESULT_SNAPSHOT_SIZE,
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
            if not result_df.empty:
                for col in ["created_at", "modified_at"]:
                    if col in result_df.columns:
                        result_df[col] = pd.to_datetime(result_df[col], errors="coerce").dt.floor("min")
                    else:
                        result_df[col] = pd.NaT

                if "filesize_bytes" in result_df.columns:
                    result_df["filesize"] = result_df["filesize_bytes"].apply(human_readable_size)
                else:
                    result_df["filesize_bytes"] = pd.NA
                    result_df["filesize"] = ""

            st.session_state["result_snapshot_df"] = result_df.copy()
            st.session_state["working_result_df"] = result_df.copy()
            st.session_state["result_total"] = total
            st.session_state["local_page"] = 1
            st.session_state["refine_query"] = ""

        st.success(f"총 {total}건")
       

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
base_df = st.session_state.get("result_snapshot_df")
working_df = st.session_state.get("working_result_df")  

if isinstance(base_df, pd.DataFrame):
    st.success(
        f"ES 전체 {st.session_state.get('result_total', 0):,}건 중 "
        f"상위 {len(base_df):,}건 snapshot 저장 / "
        f"현재 작업 결과 {len(working_df) if isinstance(working_df, pd.DataFrame) else 0:,}건"
    )
    st.caption(f"※ 결과 내 검색은 ES 상위 {len(base_df):,}건 snapshot 기준으로 동작합니다.")

    with st.container():
        st.markdown("### 결과 내 검색")

        r1, r2, r3, r4 = st.columns([4, 1, 1, 1])

        with r1:
            st.text_input(
                "결과 내 검색어",
                key="refine_query",
                placeholder="파일명 / 경로 / 확장자 기준으로 현재 결과를 다시 좁힙니다."
            )

        if r2.button("원본 기준", use_container_width=True):
            source_df = st.session_state.get("result_snapshot_df")
            st.session_state["working_result_df"] = apply_refine_filter(
                source_df,
                st.session_state.get("refine_query", "")
            )
            st.session_state["local_page"] = 1
            st.rerun()

        if r3.button("현재 결과 축소", use_container_width=True):
            source_df = st.session_state.get("working_result_df")
            st.session_state["working_result_df"] = apply_refine_filter(
                source_df,
                st.session_state.get("refine_query", "")
            )
            st.session_state["local_page"] = 1
            st.rerun()

        if r4.button("복구", use_container_width=True):
            source_df = st.session_state.get("result_snapshot_df")
            st.session_state["working_result_df"] = source_df.copy() if source_df is not None else None
            st.session_state["refine_query"] = ""
            st.session_state["local_page"] = 1
            st.rerun()
    if working_df is None or working_df.empty:
        st.info("검색 결과가 없습니다.")
    else:
        page_size = int(st.session_state.get("size", cfg.default_size))
        local_page = int(st.session_state.get("local_page", 1))

        paged_df, local_page, local_total = paginate_local_df(
            working_df,
            local_page,
            page_size,
        )

        display_df = paged_df[[
            "filename",
            "path_real",
            "extension",
            "filesize",
            "created_at",
            "modified_at",
        ]].copy()

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
            key=f"aggrid_result_local_{local_page}",
        )

        selected_rows = grid_response.get("selected_rows", [])

        if isinstance(selected_rows, pd.DataFrame):
            selected_rows = selected_rows.to_dict("records")

        if selected_rows:
            selected_path = str(selected_rows[0].get("path_real", "") or "")
            st.session_state["selected_path_display"] = selected_path
        else:
            st.session_state["selected_path_display"] = ""

        if st.session_state.get("selected_path_display"):
            st.markdown("#### 선택한 파일 경로")
            st.text_input(
                "전체 경로",
                key="selected_path_display",
                label_visibility="collapsed",
            )

        new_page = render_pagination(
            total=local_total,
            page=local_page,
            size=page_size,
            window=7,
        )

        if new_page != local_page:
            st.session_state["local_page"] = new_page
            st.rerun()
