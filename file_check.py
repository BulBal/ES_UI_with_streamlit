#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
NAS 사전 점검 스크립트 (메타 기반)
- 하위 디렉토리 파일 개수/확장자 분포
- 파일명 토큰 수(예상치) 및 토큰 언어 비율(한글/영문/숫자/기타)
- 경로 깊이 통계
- (옵션) 결과 CSV 저장

주의:
- 이 스크립트는 Elasticsearch/Nori 토큰을 "정확히" 재현하지 않고,
  파일명/경로 특성 분석을 위한 "예상치"를 제공합니다.
"""

import argparse
import csv
import os
import re
import sys
from collections import Counter, defaultdict
from dataclasses import dataclass
from statistics import mean, median
from typing import Optional, List, Pattern



# ----------------------------
# Tokenization heuristics
# ----------------------------

# 구분자(파일명에 흔한 것) -> 공백으로 치환하는 느낌
SEP_RE = re.compile(r"[\s\-_./\\(){}\[\],;:+~`'\"!?@#$%^&*=<>|]+")
# CamelCase 분리 보조: "FinalReportV2" -> "Final Report V2"
CAMEL_SPLIT_RE = re.compile(r"(?<=[a-z])(?=[A-Z])")
# 숫자-문자 경계 분리: "v2Final" "Final2" -> 분리 힌트
ALNUM_BOUNDARY_RE = re.compile(r"(?<=[A-Za-z])(?=[0-9])|(?<=[0-9])(?=[A-Za-z])")

# 토큰 분류
KOREAN_RE = re.compile(r"^[가-힣]+$")
EN_RE = re.compile(r"^[A-Za-z]+$")
NUM_RE = re.compile(r"^[0-9]+$")
ALNUM_RE = re.compile(r"^[A-Za-z0-9]+$")

INCLUDE_EXTS = {
    # 이미지(3)
    "jpg", "jpeg", "png", "gif", "bmp", "tif", "tiff", "webp", "svg", "heic", "ai", "ico", "psd",

    # 문서(4)
    "pdf", "txt", "md", "rtf",
    "doc", "docx", "ppt", "pptx", "xls", "xlsx", "xlsm","csv",
    "hwp", "hwpx",

    # 설정/구성(5)
    "cfg", "conf", "ini", "properties", "policy", "info", "manifest",
    "yml", "yaml", "json", "xml", "toml", "env",

    # 압축/패키징(7)
    "zip", "7z", "rar", "tar", "gz", "tgz", "bz2", "xz", "iso", "cab",

    # old 확장자 포함
    "old",
}

DEFAULT_NAME_DENY = [
    r"(?i)^~\$",                 # Office temp (prefix "~$")
    r"(?i)^thumbs\.db$",         # Windows thumbnails
    r"(?i)^desktop\.ini$",       # Windows desktop config
]
DEFAULT_PATH_DENY = [
    r"(?i)^@eadir/",                     # Synology thumbnail dir
    r"(?i)^#recycle/",                   # Synology recycle bin
    r"(?i)^@recycle/",                   # Synology recycle bin
    r"(?i)^\$recycle\.bin/",             # Windows recycle bin
    r"(?i)^system volume information/",  # Windows system folder
    r"(?i)^node_modules/",               # node deps
    r"(?i)^__pycache__/",                # python cache
    r"(?i)^\.venv/",                     # venv
]
@dataclass
class FileRow:
    path: str
    rel_path: str
    filename: str
    ext: str
    depth: int
    size_bytes: int
    tokens: list[str]

def normalize_and_tokenize_filename(name: str, *, do_camel_split: bool = True) -> list[str]:
    """
    파일명 토큰화(예상치):
    1) 확장자 제거(분석 목적상 ext 별도 집계)
    2) camelCase, 영문-숫자 경계 분리
    3) 구분자 기준 split
    4) 소문자 normalize
    """
    base = name
    # 여러 점(.)이 있을 수 있어도, 마지막 확장자는 별도 집계로 빼고 base만 토큰화
    # ex) "report.final.v2.pdf" -> base="report.final.v2"
    if "." in base:
        base = ".".join(base.split(".")[:-1]) or base  # 파일명이 ".bashrc" 같은 경우 고려

    if do_camel_split:
        base = CAMEL_SPLIT_RE.sub(" ", base)
        base = ALNUM_BOUNDARY_RE.sub(" ", base)

    base = SEP_RE.sub(" ", base).strip()
    if not base:
        return []

    toks = [t.lower() for t in base.split() if t]
    return toks

def classify_token(tok: str) -> str:
    """
    토큰 언어/타입 분류:
    - ko: 순수 한글
    - en: 순수 영문
    - num: 숫자
    - alnum: 영문+숫자 혼합(예: v2, rfp2026)
    - other: 기타(한글+영문 혼합 등)
    """
    if KOREAN_RE.match(tok):
        return "ko"
    if EN_RE.match(tok):
        return "en"
    if NUM_RE.match(tok):
        return "num"
    if ALNUM_RE.match(tok):
        return "alnum"
    return "other"

# ----------------------------
# Scan and aggregate
# ----------------------------

def iter_files(
    root: str,
    *,
    follow_symlinks: bool = False,
    include_hidden: bool = False,
    path_deny_res: Optional[List[Pattern]] = None,
):
    root = os.path.abspath(root)

    for dirpath, dirnames, filenames in os.walk(root, followlinks=follow_symlinks):
        # 1) 숨김/링크 디렉토리 제거 (os.walk 가지치기 핵심)
        dirnames[:] = [
            d for d in dirnames
            if (include_hidden or not d.startswith("."))
            and not os.path.islink(os.path.join(dirpath, d))
        ]

        # 2) path deny에 걸리는 디렉토리는 아예 내려가지 않음 (prune)
        if path_deny_res:
            kept = []
            for d in dirnames:
                sub_rel = os.path.relpath(os.path.join(dirpath, d), root).replace(os.sep, "/") + "/"
                if any(rx.search(sub_rel) for rx in path_deny_res):
                    continue
                kept.append(d)
            dirnames[:] = kept

        # 3) 숨김 파일 제거
        if not include_hidden:
            filenames = [f for f in filenames if not f.startswith(".")]

        for fn in filenames:
            full = os.path.join(dirpath, fn)

            # 파일 심볼릭 링크 제외(원하면 옵션화 가능)
            if os.path.islink(full):
                continue

            try:
                st = os.stat(full)
                size = int(st.st_size)
            except OSError:
                size = -1

            rel = os.path.relpath(full, root)
            depth = rel.count(os.sep)
            ext = os.path.splitext(fn)[1].lower().lstrip(".")
            yield full, rel, fn, ext, depth, size

def summarize_numeric(values: list[int]) -> dict:
    if not values:
        return {"min": 0, "max": 0, "mean": 0, "median": 0}
    return {
        "min": min(values),
        "max": max(values),
        "mean": mean(values),
        "median": median(values),
    }

def compile_patterns(patterns: list[str], label: str) -> list[re.Pattern]:
    compiled: list[re.Pattern] = []
    for p in patterns:
        try:
            compiled.append(re.compile(p))
        except re.error as e:
            print(f"[WARN] invalid regex in {label}: {p!r} -> {e}", file=sys.stderr)
    return compiled

import time

def main():
    ap = argparse.ArgumentParser(
        description="NAS 사전 점검: 파일 개수/확장자/파일명 토큰/언어 비율 등",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter
    )
    ap.add_argument("root", help="스캔할 루트 디렉토리")
    ap.add_argument("--max-files", type=int, default=0,
                    help="최대 몇 개 파일까지 샘플링할지(0이면 전체)")
    ap.add_argument("--include-hidden", action="store_true", help="숨김 파일/폴더 포함")
    ap.add_argument("--follow-symlinks", action="store_true", help="심볼릭 링크 따라가기")
    ap.add_argument("--min-token-len", type=int, default=2, help="이 길이 미만 토큰 제외")
    ap.add_argument("--ext-allow", nargs="*", default=[],
                    help="포함할 확장자 목록(예: pdf docx pptx). 비우면 전체")
    ap.add_argument("--ext-deny", nargs="*", default=[],
                    help="제외할 확장자 목록(예: tmp bak). 비우면 제외 없음")
    ap.add_argument("--csv", default="", help="(옵션) 파일별 요약을 CSV로 저장 경로")
    ap.add_argument("--topn", type=int, default=170, help="상위 N개 토큰/확장자 출력")
    ap.add_argument("--name-deny", nargs="*", default=[],
                help="제외할 파일명 정규식 목록(예: '^~\\$' '\\.tmp$')")
    ap.add_argument("--path-deny", nargs="*", default=[],
                help="제외할 상대경로 정규식 목록(예: '^\\.git/' '^node_modules/')")
    
    args = ap.parse_args()

    t0 = time.perf_counter()
 
    
    #default deny patterns 추가 Default 하드 코딩
    DEFAULT_PATH_DENY.append(r'(^|/)!Temporary_임시보관용-올린사람이삭제까지해주세요')
    name_deny_res = compile_patterns(DEFAULT_NAME_DENY + args.name_deny, "name-deny")
    path_deny_res = compile_patterns(DEFAULT_PATH_DENY + args.path_deny, "path-deny")
    root = args.root
    if not os.path.isdir(root):
        print(f"[ERROR] not a directory: {root}", file=sys.stderr)
        sys.exit(1)

    allow = {e.lower().lstrip(".") for e in args.ext_allow} if args.ext_allow else None
    deny = {e.lower().lstrip(".") for e in args.ext_deny} if args.ext_deny else set()

    file_count = 0
    skipped_by_ext = 0
    inaccessible = 0

    ext_counter = Counter()
    depth_list = []
    size_list = []
    token_counter = Counter()
    token_type_counter = Counter()
    tokens_per_file = []
    filename_len_list = []

    # (옵션) CSV rows
    rows: list[FileRow] = []

    for full, rel, fn, ext, depth, size in iter_files(
        root,
        follow_symlinks=args.follow_symlinks,
        include_hidden=args.include_hidden,
        path_deny_res=path_deny_res
    ):
        # 확장자 필터
        ext_norm = (ext or "").lower().lstrip(".")  # "pdf"
        # if ext_norm not in INCLUDE_EXTS:
        #     skipped_by_ext += 1
        #     continue
        # if any(rx.search(fn) for rx in name_deny_res):
        #     continue
        # if any(rx.search(rel.replace(os.sep, "/")) for rx in path_deny_res):
        #     continue

        file_count += 1
        if file_count % 200 == 0:
            print(f"[PROGRESS] processed={file_count:,} (last={rel.replace(os.sep, '/')})")
            sys.stdout.flush()
        ext_counter[ext_norm or "(no_ext)"] += 1
        depth_list.append(depth)
        filename_len_list.append(len(fn))
        if size >= 0:
            size_list.append(size)
        else:
            inaccessible += 1

        toks = normalize_and_tokenize_filename(fn, do_camel_split=True)
        toks = [t for t in toks if len(t) >= args.min_token_len]
        tokens_per_file.append(len(toks))

        for t in toks:
            token_counter[t] += 1
            token_type_counter[classify_token(t)] += 1

        if args.csv:
            rows.append(FileRow(
                path=full,
                rel_path=rel,
                filename=fn,
                ext=ext_norm or "(no_ext)",
                depth=depth,
                size_bytes=size,
                tokens=toks
            ))

        if args.max_files and file_count >= args.max_files:
            break
    
    def print_counter_grid(title: str, items: list[tuple[str, int]], cols: int = 6, *, label_width: int = 12):
        """
        items: [(name, count), ...]
        cols: 한 줄에 몇 개씩 출력할지
        label_width: 라벨(확장자) 출력 폭
        """
        print(f"\n{title}")
        if not items:
            print("  (no data)")
            return

        # count 자리수는 최대값 기준으로 폭을 맞춤
        max_cnt = max(c for _, c in items)
        cnt_width = len(f"{max_cnt:,}")

        for i in range(0, len(items), cols):
            row = items[i:i+cols]
            line_parts = []
            for ext, cnt in row:
                # 예: "      pdf: 12,345"
                part = f"{ext:>{label_width}}: {cnt:>{cnt_width},}"
                line_parts.append(part)
            print("  " + "   ".join(line_parts))
    # ----------------------------
    # Print report
    # ----------------------------
    print("\n========================")
    print("NAS 사전 점검 리포트")
    print("========================")
    print(f"Root: {os.path.abspath(root)}")
    print(f"Scanned files: {file_count:,}")
    if args.max_files:
        print(f"(sample limit applied: max {args.max_files:,} files)")
    if skipped_by_ext:
        print(f"Skipped by ext filter: {skipped_by_ext:,}")
    if inaccessible:
        print(f"Inaccessible/stat failed: {inaccessible:,}")

    print_counter_grid(
    "[1] 확장자 분포 (Top)",
    ext_counter.most_common(args.topn),
    cols=30,          # ✅ 한 줄에 8개
    label_width=15   # 기존 정렬 느낌 유지
    )

    print("\n[2] 경로 깊이(depth) 통계 (root 기준)")
    dstat = summarize_numeric(depth_list)
    print(f"  min={dstat['min']}  max={dstat['max']}  mean={dstat['mean']:.2f}  median={dstat['median']}")

    print("\n[3] 파일 크기(bytes) 통계")
    sstat = summarize_numeric(size_list)
    print(f"  min={sstat['min']:,}  max={sstat['max']:,}  mean={sstat['mean']:.2f}  median={sstat['median']:,}")

    print("\n[4] 파일명 길이(문자) 통계")
    fstat = summarize_numeric(filename_len_list)
    print(f"  min={fstat['min']}  max={fstat['max']}  mean={fstat['mean']:.2f}  median={fstat['median']}")

    print("\n[5] 파일명 토큰 수(예상치) 통계")
    tstat = summarize_numeric(tokens_per_file)
    print(f"  min={tstat['min']}  max={tstat['max']}  mean={tstat['mean']:.2f}  median={tstat['median']}")

    total_tokens = sum(token_type_counter.values())
    print("\n[6] 토큰 언어/타입 비율 (파일명 토큰 기준)")
    if total_tokens == 0:
        print("  (no tokens)")
    else:
        # ko/en 중심 + 그 외도 같이
        for k in ["ko", "en", "num", "alnum", "other"]:
            c = token_type_counter.get(k, 0)
            ratio = (c / total_tokens) * 100
            print(f"  - {k:>5}: {c:,}  ({ratio:.2f}%)")

        ko = token_type_counter.get("ko", 0)
        en = token_type_counter.get("en", 0)
        if (ko + en) > 0:
            print(f"  * ko:en (pure only) = {ko}:{en}  (ko {ko/(ko+en)*100:.1f}%, en {en/(ko+en)*100:.1f}%)")

    print_counter_grid(
        "[7] 상위 토큰 (Top)",
        token_counter.most_common(args.topn),
        cols=15,
        label_width=8
    )

    # ----------------------------
    # Optional CSV export
    # ----------------------------
    if args.csv:
        out = args.csv
        os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
        with open(out, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["rel_path", "filename", "ext", "depth", "size_bytes", "token_count", "tokens"])
            for r in rows:
                w.writerow([r.rel_path, r.filename, r.ext, r.depth, r.size_bytes, len(r.tokens), " ".join(r.tokens)])
        print(f"\n[CSV] saved: {out}")

    print("\nDone.\n")
    t1 = time.perf_counter()  # ✅ 끝
    elapsed = t1 - t0
    print(f"[TIME] elapsed: {elapsed:.2f}s ({elapsed/60:.2f} min)")

if __name__ == "__main__":
    main()
