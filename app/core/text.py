"""사용자 노출 텍스트의 신뢰경계 정제 유틸리티."""

from __future__ import annotations

import re

# 비-whitespace 제어문자(C0/C1: NUL·ESC·DEL 등 — \t\n\r 은 아래 WS 접기로 넘김)와
# zero-width·bidi 포맷 문자(ZWSP·RTL override 등) 제거 — ANSI 이스케이프·양방향 조작 방어.
_CTRL = re.compile(
    r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f\u200b-\u200f\u202a-\u202e\u2060-\u206f\ufeff]"
)
_WS_RUN = re.compile(r"\s+")


def _strip_unsafe(text: str) -> str:
    """제어·zero-width·bidi 포맷 문자를 제거하고 공백류를 단일 공백으로 접는다."""
    stripped = _CTRL.sub("", text)
    return _WS_RUN.sub(" ", stripped).strip()


def _strip_unsafe_multiline(text: str) -> str:
    """장문의 구조적 개행은 보존하면서 각 줄에 `_strip_unsafe`를 적용한다."""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    lines: list[str] = []
    for line in normalized.split("\n"):
        stripped = _CTRL.sub("", line)
        indent_len = len(stripped) - len(stripped.lstrip(" "))
        body = _strip_unsafe(stripped[indent_len:])
        lines.append((" " * indent_len) + body if body else "")
    cleaned = "\n".join(lines)
    return cleaned.strip("\n")
