"""일반 대화 폴백 서브그래프 (이슈 #2 MVP 슬라이스).

추천/장바구니/상품질문 어디에도 해당하지 않는 무관한 질의를 처리한다. decompose 가
intent=general 로 판별하고 reply 를 함께 산출하므로(별도 LLM 호출 없음, EX-7) 그 답변을
token 으로 스트리밍한다. 결정 18(일반 대화 중 조건부 추천 유도)은 후속 SPEC 소관.
"""

from __future__ import annotations

from collections.abc import AsyncIterator

from app.agents.buyer._frames import sse
from app.core.text import _strip_unsafe
from app.schemas.chat import TokenData


async def stream_fallback(decision, *, observer=None) -> AsyncIterator[str]:
    """일반 대화 답변을 token 으로 스트리밍한다(done 은 상위 buyer 그래프가 emit)."""
    text = decision.reply or "찾으시는 상품이 있으면 말씀해 주세요. 예: '5만원 이하 무선 이어폰'"
    yield sse("token", TokenData(text=_strip_unsafe(text)).model_dump(by_alias=True))
