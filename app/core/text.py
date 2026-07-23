"""사용자 노출 텍스트의 신뢰경계 정제 유틸리티."""

from __future__ import annotations

import re
from dataclasses import dataclass

from app.core.unicode_security import (
    is_tag_character,
    is_variation_selector,
    strip_invalid_invisible_sequences,
)

# 비-whitespace 제어문자(C0/C1: NUL·ESC·DEL 등 — \t\n\r 은 아래 WS 접기로 넘김)와
# zero-width·bidi 포맷 문자(ZWSP·RTL override 등) 제거 — ANSI 이스케이프·양방향 조작 방어.
_CTRL = re.compile(
    r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f\u200b-\u200f\u202a-\u202e\u2060-\u206f\ufeff]"
)
_WS_RUN = re.compile(r"\s+")


def _strip_unsafe_controls(text: str) -> str:
    """Unicode 문맥 검사가 끝난 한 줄에서 제어문자와 중복 공백을 제거한다."""
    return _WS_RUN.sub(" ", _CTRL.sub("", text)).strip()


def _strip_unsafe(text: str) -> str:
    """제어·zero-width·bidi 포맷 문자를 제거하고 공백류를 단일 공백으로 접는다."""
    return _strip_unsafe_controls(strip_invalid_invisible_sequences(text))


def _strip_unsafe_multiline_controls(text: str) -> str:
    """Unicode 문맥 검사가 끝난 장문에서 제어문자를 제거하고 개행을 보존한다."""
    normalized = text.replace("\r\n", "\n").replace("\r", "\n")
    lines: list[str] = []
    for line in normalized.split("\n"):
        stripped = _CTRL.sub("", line)
        indent_len = len(stripped) - len(stripped.lstrip(" "))
        body = _strip_unsafe_controls(stripped[indent_len:])
        lines.append((" " * indent_len) + body if body else "")
    cleaned = "\n".join(lines)
    return cleaned.strip("\n")


def _strip_unsafe_multiline(text: str) -> str:
    """장문의 구조적 개행을 보존하면서 Unicode·제어문자를 정제한다."""
    return _strip_unsafe_multiline_controls(strip_invalid_invisible_sequences(text))


@dataclass(frozen=True, slots=True)
class SecuritySkeleton:
    """은닉 문자를 뺀 검사 문자열과 원문 위치 매핑."""

    text: str
    source: str
    source_starts: tuple[int, ...]

    def source_span(self, start: int, end: int) -> tuple[int, int]:
        """skeleton 범위를 대응하는 원문 범위로 변환한다."""
        source_start = self.source_starts[start]
        source_end = self.source_starts[end] if end < len(self.source_starts) else len(self.source)
        return source_start, source_end


def _security_skeleton(text: str) -> SecuritySkeleton:
    """보안 검사 전용으로 제어·VS·Tag를 제거하되 원문 위치를 기록한다."""
    characters: list[str] = []
    source_starts: list[int] = []
    for index, char in enumerate(text):
        if _CTRL.fullmatch(char) or is_variation_selector(char) or is_tag_character(char):
            continue
        characters.append(char)
        source_starts.append(index)
    return SecuritySkeleton("".join(characters), text, tuple(source_starts))
