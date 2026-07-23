"""Unicode Variation Selector·Tag 문맥 검사 (이슈 #72)."""

from __future__ import annotations

import base64
import sys
import zlib
from array import array
from bisect import bisect_left

from app.core._unicode_variation_data import (
    COMPRESSED_VARIATION_KEYS_B85,
    VARIATION_KEY_COUNT,
)


def _selector_index(char: str) -> int | None:
    codepoint = ord(char)
    if 0xFE00 <= codepoint <= 0xFE0F:
        return codepoint - 0xFE00
    if 0xE0100 <= codepoint <= 0xE01EF:
        return 16 + codepoint - 0xE0100
    return None


def _load_registered_keys() -> array[int]:
    packed = zlib.decompress(base64.b85decode(COMPRESSED_VARIATION_KEYS_B85))
    keys = array("I")
    keys.frombytes(packed)
    if sys.byteorder == "little":
        keys.byteswap()
    if len(keys) != VARIATION_KEY_COUNT:
        raise RuntimeError("Unicode variation data record count mismatch")
    return keys


_REGISTERED_VARIATION_KEYS = _load_registered_keys()

_ENGLAND_FLAG = "\U0001f3f4\U000e0067\U000e0062\U000e0065\U000e006e\U000e0067\U000e007f"
_SCOTLAND_FLAG = "\U0001f3f4\U000e0067\U000e0062\U000e0073\U000e0063\U000e0074\U000e007f"
_WALES_FLAG = "\U0001f3f4\U000e0067\U000e0062\U000e0077\U000e006c\U000e0073\U000e007f"
_SUPPORTED_RGI_TAG_SEQUENCES = (_ENGLAND_FLAG, _SCOTLAND_FLAG, _WALES_FLAG)


def is_variation_selector(char: str) -> bool:
    """Issue #72 대상 Variation Selector이면 True."""
    return _selector_index(char) is not None


def is_registered_variation_sequence(base: str, selector: str) -> bool:
    """공식 데이터에 등록된 정확한 `(base, selector)` 쌍인지 판정한다."""
    if len(base) != 1 or len(selector) != 1:
        return False
    selector_index = _selector_index(selector)
    if selector_index is None:
        return False
    key = (ord(base) << 8) | selector_index
    index = bisect_left(_REGISTERED_VARIATION_KEYS, key)
    return index < len(_REGISTERED_VARIATION_KEYS) and _REGISTERED_VARIATION_KEYS[index] == key


def is_tag_character(char: str) -> bool:
    """Issue #72 대상 Unicode Tag 문자이면 True."""
    return 0xE0000 <= ord(char) <= 0xE007F


def strip_invalid_invisible_sequences(text: str) -> str:
    """등록되지 않은 Variation Selector·Tag 시퀀스를 제거한다."""
    output: list[str] = []
    available_base: str | None = None
    index = 0
    while index < len(text):
        supported_tag_sequence = next(
            (
                sequence
                for sequence in _SUPPORTED_RGI_TAG_SEQUENCES
                if text.startswith(sequence, index)
            ),
            None,
        )
        if supported_tag_sequence is not None:
            output.append(supported_tag_sequence)
            index += len(supported_tag_sequence)
            available_base = None
            continue

        char = text[index]
        index += 1
        if is_variation_selector(char):
            if available_base is not None and is_registered_variation_sequence(
                available_base, char
            ):
                output.append(char)
            available_base = None
            continue
        if is_tag_character(char):
            available_base = None
            continue
        output.append(char)
        available_base = char
    return "".join(output)


_TAG_BASE = "\U0001f3f4"


class UnicodeSequenceStreamSanitizer:
    """청크 경계에 걸친 Unicode VS·Tag 시퀀스를 문맥에 맞게 정제한다."""

    def __init__(self) -> None:
        self._available_base: str | None = None
        self._pending_tag_sequence: str | None = None

    def feed(self, text: str) -> str:
        """확정된 문자는 즉시 내보내고 미완성 RGI Tag prefix만 보류한다."""
        output: list[str] = []
        for char in text:
            self._consume(char, output)
        return "".join(output)

    def _consume(self, char: str, output: list[str]) -> None:
        if self._pending_tag_sequence is not None:
            if is_tag_character(char):
                candidate = self._pending_tag_sequence + char
                if any(sequence.startswith(candidate) for sequence in _SUPPORTED_RGI_TAG_SEQUENCES):
                    self._pending_tag_sequence = candidate
                    if candidate in _SUPPORTED_RGI_TAG_SEQUENCES:
                        output.append(candidate)
                        self._pending_tag_sequence = None
                        self._available_base = None
                    return
                output.append(_TAG_BASE)
                self._pending_tag_sequence = None
                self._available_base = None
                return

            had_tag_payload = len(self._pending_tag_sequence) > 1
            output.append(_TAG_BASE)
            self._pending_tag_sequence = None
            self._available_base = None if had_tag_payload else _TAG_BASE

        if char == _TAG_BASE:
            self._pending_tag_sequence = _TAG_BASE
            self._available_base = None
            return
        if is_variation_selector(char):
            if self._available_base is not None and is_registered_variation_sequence(
                self._available_base, char
            ):
                output.append(char)
            self._available_base = None
            return
        if is_tag_character(char):
            self._available_base = None
            return
        output.append(char)
        self._available_base = char

    def flush(self) -> str:
        """스트림 종료 시 미완성 Tag payload는 버리고 visible base만 내보낸다."""
        ready = _TAG_BASE if self._pending_tag_sequence is not None else ""
        self._pending_tag_sequence = None
        self._available_base = None
        return ready
