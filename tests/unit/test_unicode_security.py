"""Unicode Variation Selector·Tag 출력 하드닝 회귀 테스트 (이슈 #72)."""

from app.core.text import _strip_unsafe
from app.core.unicode_security import UnicodeSequenceStreamSanitizer


def test_strip_unsafe_removes_unregistered_variation_pair() -> None:
    """등록되지 않은 ASCII+VS 조합은 visible base만 남긴다."""
    assert _strip_unsafe("A\ufe0fB") == "AB"


ENGLAND_FLAG = "\U0001f3f4\U000e0067\U000e0062\U000e0065\U000e006e\U000e0067\U000e007f"
SCOTLAND_FLAG = "\U0001f3f4\U000e0067\U000e0062\U000e0073\U000e0063\U000e0074\U000e007f"
WALES_FLAG = "\U0001f3f4\U000e0067\U000e0062\U000e0077\U000e006c\U000e0073\U000e007f"


def test_generated_variation_data_is_pinned_and_nonempty() -> None:
    """런타임 lookup은 고정 공식 데이터에서 생성되고 충분한 등록 pair를 가진다."""
    from app.core import _unicode_variation_data as data

    assert data.UNICODE_VERSION == "17.0.0"
    assert data.IVD_VERSION == "2025-07-14"
    assert data.VARIATION_KEY_COUNT > 30_000
    assert set(data.SOURCE_SHA256) == {"standardized", "emoji", "ivd"}


def test_strip_unsafe_removes_orphan_and_repeated_variation_selectors() -> None:
    """고아·반복 selector와 supplemental payload는 visible text에서 제거한다."""
    dirty = "\ufe0f시작 ❤\ufe0f\ufe0e 상품\U000e0158\U000e0155"
    assert _strip_unsafe(dirty) == "시작 ❤️ 상품"


def test_strip_unsafe_preserves_registered_variation_sequences() -> None:
    """정상 emoji/keycap/CJK variation sequence는 코드포인트 그대로 보존한다."""
    text = "❤️ #️⃣ 㐂\U000e0100"
    assert _strip_unsafe(text) == text


def test_strip_unsafe_preserves_supported_rgi_tag_flags() -> None:
    """지원 정책으로 고정한 England·Scotland·Wales tag flag는 보존한다."""
    text = f"{ENGLAND_FLAG} {SCOTLAND_FLAG} {WALES_FLAG}"
    assert _strip_unsafe(text) == text


def test_strip_unsafe_drops_ill_formed_and_unsupported_tag_payloads() -> None:
    """고아·미종결·비지원 tag는 visible base만 남긴다."""
    dirty = (
        f"A\U000e0075\U000e0073\U000e007fB \U0001f3f4\U000e0075\U000e0073 {ENGLAND_FLAG}\U000e0061"
    )
    assert _strip_unsafe(dirty) == f"AB 🏴 {ENGLAND_FLAG}"


def test_stream_sanitizer_preserves_sequences_split_across_chunks() -> None:
    """청크 경계의 등록 VS·RGI Tag 시퀀스를 원문 그대로 보존한다."""
    guard = UnicodeSequenceStreamSanitizer()
    parts = [
        guard.feed("좋아 ❤"),
        guard.feed("️ " + ENGLAND_FLAG[:3]),
        guard.feed(ENGLAND_FLAG[3:]),
        guard.flush(),
    ]
    assert "".join(parts) == "좋아 ❤️ " + ENGLAND_FLAG


def test_stream_sanitizer_drops_invalid_sequences_split_across_chunks() -> None:
    """청크를 가로지르는 미등록 VS·Tag payload도 visible base만 남긴다."""
    guard = UnicodeSequenceStreamSanitizer()
    parts = [
        guard.feed("A"),
        guard.feed("️B 🏴\U000e0075"),
        guard.feed("\U000e0073"),
        guard.flush(),
    ]
    assert "".join(parts) == "AB 🏴"
