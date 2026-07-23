"""공식 Unicode 등록 variation sequence를 compact Python 모듈로 생성한다.

런타임 네트워크 의존을 만들지 않기 위한 개발 도구다. 입력 버전과 URL은 이 파일에
고정하며, 생성 결과에는 각 원본의 SHA-256을 기록한다(이슈 #72).
"""

from __future__ import annotations

import argparse
import base64
import hashlib
import struct
import textwrap
import urllib.request
import zlib
from pathlib import Path

UNICODE_VERSION = "17.0.0"
IVD_VERSION = "2025-07-14"
SOURCE_URLS = {
    "standardized": ("https://www.unicode.org/Public/17.0.0/ucd/StandardizedVariants.txt"),
    "emoji": ("https://www.unicode.org/Public/17.0.0/ucd/emoji/emoji-variation-sequences.txt"),
    "ivd": "https://www.unicode.org/ivd/data/2025-07-14/IVD_Sequences.txt",
}
_DEFAULT_OUTPUT = Path(__file__).resolve().parents[1] / "app/core/_unicode_variation_data.py"


def _selector_index(codepoint: int) -> int | None:
    """Issue #72 대상 selector를 0..255 인덱스로 정규화한다."""
    if 0xFE00 <= codepoint <= 0xFE0F:
        return codepoint - 0xFE00
    if 0xE0100 <= codepoint <= 0xE01EF:
        return 16 + codepoint - 0xE0100
    return None


def _parse_variation_keys(payload: bytes, *, source_name: str) -> set[int]:
    """Unicode data 파일에서 대상 `(base << 8) | selector_index` 집합을 읽는다."""
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError as exc:
        raise ValueError(f"{source_name}: UTF-8 디코딩 실패") from exc

    keys: set[int] = set()
    data_lines = 0
    for line_number, line in enumerate(text.splitlines(), start=1):
        raw = line.split("#", 1)[0].strip()
        if not raw:
            continue
        data_lines += 1
        codepoints = raw.split(";", 1)[0].strip().split()
        if len(codepoints) != 2:
            continue
        try:
            base, selector = (int(value, 16) for value in codepoints)
        except ValueError as exc:
            raise ValueError(f"{source_name}:{line_number}: 잘못된 코드포인트") from exc
        selector_index = _selector_index(selector)
        if selector_index is not None:
            keys.add((base << 8) | selector_index)

    if data_lines == 0 or not keys:
        raise ValueError(f"{source_name}: 유효한 variation sequence가 없습니다")
    return keys


def _fetch(url: str) -> bytes:
    request = urllib.request.Request(url, headers={"User-Agent": "jarvis-ai-unicode-generator"})
    with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310 - 고정 HTTPS URL
        return response.read()


def _render_module(keys: list[int], source_hashes: dict[str, str]) -> str:
    packed = b"".join(struct.pack(">I", key) for key in keys)
    encoded = base64.b85encode(zlib.compress(packed, level=9)).decode("ascii")
    encoded_lines = "\n".join(f'    "{chunk}"' for chunk in textwrap.wrap(encoded, width=88))
    url_lines = "\n".join(f'    "{name}": "{url}",' for name, url in SOURCE_URLS.items())
    hash_lines = "\n".join(f'    "{name}": "{source_hashes[name]}",' for name in SOURCE_URLS)
    return f'''"""자동 생성 파일 — 직접 수정하지 말고 scripts/generate_unicode_security_data.py를 실행한다."""

UNICODE_VERSION = "{UNICODE_VERSION}"
IVD_VERSION = "{IVD_VERSION}"
SOURCE_URLS = {{
{url_lines}
}}
SOURCE_SHA256 = {{
{hash_lines}
}}
VARIATION_KEY_COUNT = {len(keys)}
COMPRESSED_VARIATION_KEYS_B85 = (
{encoded_lines}
)
'''


def generate(output: Path) -> None:
    """고정 원본을 내려받아 결정론적 generated module을 쓴다."""
    all_keys: set[int] = set()
    source_hashes: dict[str, str] = {}
    for name, url in SOURCE_URLS.items():
        payload = _fetch(url)
        source_hashes[name] = hashlib.sha256(payload).hexdigest()
        all_keys.update(_parse_variation_keys(payload, source_name=name))

    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(_render_module(sorted(all_keys), source_hashes), encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=_DEFAULT_OUTPUT)
    args = parser.parse_args()
    generate(args.output.resolve())


if __name__ == "__main__":
    main()
