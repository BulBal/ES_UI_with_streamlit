from __future__ import annotations

import re
from enum import Enum


class ExtensionCategory(Enum):
    IMAGE = (
        "이미지",
        (
            "jpg",
            "jpeg",
            "png",
            "gif",
            "bmp",
            "tif",
            "tiff",
            "webp",
            "svg",
            "heic",
            "ai",
            "ico",
            "psd",
        ),
    )
    DOCUMENT = (
        "문서",
        (
            "pdf",
            "txt",
            "md",
            "rtf",
            "doc",
            "docx",
            "ppt",
            "pptx",
            "xls",
            "xlsx",
            "xlsm",
            "csv",
            "hwp",
            "hwpx",
        ),
    )
    CONFIG = (
        "설정 / 구성",
        (
            "conf",
            "properties",
            "policy",
            "manifest",
            "yml",
            "yaml",
            "json",
            "xml",
            "toml",
            "env",
        ),
    )
    ARCHIVE = (
        "압축 / 패키징",
        (
            "zip",
            "7z",
            "rar",
            "tar",
            "gz",
            "tgz",
            "bz2",
            "xz",
            "iso",
            "cab",
        ),
    )
    OTHER = ("기타", ("old",))

    @property
    def label(self) -> str:
        return self.value[0]

    @property
    def extensions(self) -> tuple[str, ...]:
        return self.value[1]


SUPPORTED_EXTENSIONS = tuple(
    ext for category in ExtensionCategory for ext in category.extensions
)


def build_extension_help() -> str:
    lines = ["### 지원 확장자 목록", ""]
    for index, category in enumerate(ExtensionCategory, start=1):
        lines.append(f"**{index}. {category.label}**")
        for ext in category.extensions:
            lines.append(f"- `*.{ext}`")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


EXTENSION_HELP = build_extension_help()


def parse_extensions(ext_str: str) -> list[str]:
    if not ext_str:
        return []

    parts = re.split(r"[,\s;/|]+", ext_str.lower())
    cleaned: list[str] = []

    for part in parts:
        item = part.strip().lstrip(".")
        if item:
            cleaned.append(item)

    return list(dict.fromkeys(cleaned))
