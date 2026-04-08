from __future__ import annotations

import json
import threading
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional
from urllib.parse import urlparse

import streamlit as st


@dataclass
class TranscriptStore:
    """단일 사용자/탭 PoC를 위한 최신 transcript 저장소."""

    lock: threading.Lock
    latest: Optional[str] = None

    def set(self, text: str) -> None:
        cleaned = text.strip()
        if not cleaned:
            return
        with self.lock:
            self.latest = cleaned

    def pop(self) -> Optional[str]:
        with self.lock:
            text = self.latest
            self.latest = None
            return text


_STORE = TranscriptStore(lock=threading.Lock())


class _TranscriptHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 (BaseHTTPRequestHandler 규약)
        parsed = urlparse(self.path)

        if parsed.path != "/transcript":
            self.send_response(404)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.end_headers()
            self.wfile.write(json.dumps({"error": "not found"}).encode("utf-8"))
            return

        payload = {"text": _STORE.pop()}
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")

        self.send_response(200)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        # Python 서버에서 요청하는 용도라 CORS는 본질적으로 필요 없지만, PoC 편의를 위해 허용한다.
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args) -> None:  # noqa: A002
        # 기본 stdout 로깅을 억제한다 (PoC에서 UI 출력 오염 방지).
        return


@st.cache_resource
def _start_transcript_api_server(host: str, port: int) -> ThreadingHTTPServer:
    """/transcript 엔드포인트를 제공하는 HTTP 서버를 1회 기동한다."""

    server = ThreadingHTTPServer((host, port), _TranscriptHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server


def main() -> None:
    st.set_page_config(page_title="STT 서버", layout="wide")
    st.title("음성 인식 서버 (브라우저 기반, PoC)")

    api_host = st.secrets.get("stt_api_host") if "stt_api_host" in st.secrets else None
    api_port = st.secrets.get("stt_api_port") if "stt_api_port" in st.secrets else None

    resolved_host = str(api_host) if api_host else "0.0.0.0"
    try:
        resolved_port = int(api_port) if api_port else 8502
    except ValueError:
        resolved_port = 8502

    _start_transcript_api_server(resolved_host, resolved_port)

    # 사용자가 검색 UI(core/stt_client.py)에서 접근할 수 있도록 안내용 URL은 별도 분리한다.
    api_base_url = st.secrets.get("stt_api_base_url") if "stt_api_base_url" in st.secrets else None
    public_base = str(api_base_url) if api_base_url else f"http://localhost:{resolved_port}"

    st.caption(f"transcript API: {public_base}/transcript")

    st.write(
        """이 앱은 브라우저의 Web Speech API(SpeechRecognition)에 의존합니다.
검색 UI에서는 이 앱의 /transcript 엔드포인트를 폴링하여 인식 결과를 가져옵니다."""
    )

    voice_component = st.components.v2.component(
        name="stt_server_voice_v2_minimal",
        html="""
        <div class="voice-container">
          <button id="voice-button" type="button" title="음성 입력">🎤</button>
          <span id="voice-status" class="status-text"></span>
        </div>
        """,
        css="""
        .voice-container {
          display: flex;
          align-items: center;
          gap: 10px;
          height: 48px;
        }
        #voice-button {
          width: 44px;
          height: 44px;
          border: 1px solid rgba(0,0,0,0.2);
          border-radius: 10px;
          background: #f3f4f6;
          font-size: 20px;
          cursor: pointer;
        }
        #voice-button.listening {
          background: #ef4444;
          color: #ffffff;
        }
        .status-text {
          font-size: 13px;
          color: rgba(0,0,0,0.6);
        }
        """,
        js="""
        export default function(component) {
          const { parentElement, data, setStateValue } = component;
          const button = parentElement.querySelector('#voice-button');
          const statusEl = parentElement.querySelector('#voice-status');
          if (!button || !statusEl) return;

          const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition;
          if (!SpeechRecognition) {
            setStateValue('status', 'unsupported');
            setStateValue('error', 'SpeechRecognition not supported');
            statusEl.textContent = '지원되지 않는 브라우저';
            button.disabled = true;
            return;
          }

          const lang = (data && data.lang) || 'ko-KR';

          // 전역 재사용 (Streamlit 리런/iframe 재생성에 대비)
          const app = window.__sttServerApp || (window.__sttServerApp = {});
          if (!app.recognition) {
            app.recognition = new SpeechRecognition();
            app.recognition.continuous = false;
            app.recognition.interimResults = false;
            app.recognition.maxAlternatives = 1;

            // Chrome 계열이 제공하는 경우에만 설정 (실험적)
            try { app.recognition.processLocally = true; } catch (e) {}
          }

          const recognition = app.recognition;
          let isListening = app.isListening || false;
          let lastFinalText = app.lastFinalText || null;

          function render() {
            button.classList.toggle('listening', isListening);
            statusEl.textContent = isListening ? '듣는 중…' : '대기';
          }

          recognition.lang = lang;

          recognition.onstart = () => {
            isListening = true;
            app.isListening = true;
            setStateValue('status', 'listening');
            render();
          };

          recognition.onerror = (event) => {
            const msg = event && event.error ? event.error : 'unspecified-error';
            setStateValue('status', 'error');
            setStateValue('error', String(msg));
            isListening = false;
            app.isListening = false;
            render();
          };

          recognition.onend = () => {
            isListening = false;
            app.isListening = false;
            setStateValue('status', 'idle');
            render();
          };

          recognition.onresult = (event) => {
            let finalText = '';
            for (let i = event.resultIndex; i < event.results.length; i++) {
              const res = event.results[i];
              if (res && res.isFinal && res[0] && res[0].transcript) {
                finalText += res[0].transcript;
              }
            }
            finalText = (finalText || '').trim();
            if (finalText && finalText !== lastFinalText) {
              lastFinalText = finalText;
              app.lastFinalText = finalText;
              setStateValue('last_transcript', finalText);
              setStateValue('status', 'final');
            }
          };

          button.onclick = () => {
            if (isListening) {
              try { recognition.stop(); } catch (e) {}
              return;
            }
            try {
              setStateValue('error', null);
              setStateValue('last_transcript', null);
              recognition.lang = lang;
              recognition.start();
            } catch (err) {
              setStateValue('status', 'start-failed');
              setStateValue('error', err && err.message ? err.message : String(err));
            }
          };

          render();
        }
        """,
    )

    result = voice_component(
        key="stt_server_voice_component",
        data={"lang": "ko-KR"},
        default={"status": "idle", "error": None, "last_transcript": None},
        width="content",
        height="content",
    )

    st.subheader("최근 인식 결과")
    last_transcript_value = getattr(result, "last_transcript", None)
    if isinstance(last_transcript_value, str) and last_transcript_value.strip():
        text = last_transcript_value.strip()
        st.write(text)

        # /transcript 엔드포인트에서 1회 수신 후 제거되는 저장소에 넣는다.
        _STORE.set(text)
    else:
        st.write("결과 없음")


if __name__ == "__main__":
    main()