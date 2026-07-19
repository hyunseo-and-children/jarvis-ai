"""프로필 승격 게이트 (SPEC-PROFILE-001 §6.3, 결정 4-A).

승격 조건: salience(현저성) AND (explicitness(명시성) OR repetition-EMA(반복성 confidence)).
매 발화 자동 write 는 금지하고, "기억해" 명시 명령만 hot-path 즉시 기록한다(REQ-PROF).
임계값은 config 주입(profile_gate_threshold, 하드코딩 금지).
"""

from __future__ import annotations

import re

# "기억해" 계열 명시 **명령** 마커(줘/둬/주세요 등 저장 명령형) — hot-path 즉시 승격 트리거.
# 뒤에 한글 활용 음절이 붙으면 제외(기억해줘야/기억해줘도/기억해줘봤자 등 비명령 오탐 방지, 정밀도 우선).
_REMEMBER_RE = re.compile(r"기억해\s?(?:줘|둬|주세요|두세요)(?![가-힣])|remember\s+th(?:is|at)")


def should_promote(
    *,
    salience: float,
    explicit: bool,
    repetition_ema: float,
    threshold: float = 0.5,
) -> bool:
    """델타 후보를 장기 프로필로 승격할지 판단한다 (§6.3).

    게이트 규칙: salience 충족 AND (명시적 OR 반복성 EMA 충족).
    """
    salient = salience >= threshold
    repeated = repetition_ema >= threshold
    return salient and (explicit or repeated)


def is_remember_command(text: str | None) -> bool:
    """발화가 "기억해"류 명시 **명령**인지 — hot-path 즉시 기록 트리거(REQ-PROF).

    저장 명령형 마커(줘/둬/주세요)만 매칭한다 — 바레 "기억해"("기억해내다" 등 비명령)는 제외해
    오탐을 줄인다. 명령 마커가 있으면 같은 턴에 물음표가 섞여도 명령으로 인식한다(자연 대화 패턴).
    """
    if not text:
        return False
    # 명령 마커가 문장 끝/구두점 앞(활용형 아님)일 때만 명령으로 인식 — 같은 턴에 질문이 섞여도 인식.
    return bool(_REMEMBER_RE.search(text.lower()))
