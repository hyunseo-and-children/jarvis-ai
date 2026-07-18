"""구매자 챗봇 그래프 진입점 (스텁).

흐름 (product.md 결정 12-A / structure.md §3, 확정 2026-07-15):
    entry → 프로필 조회(reader, 동기, 내부 전용) → intent router →
        conditional edge 로 서브그래프 분기:
            - recommendation (SPEC-RECOMMEND-001): decompose→search(Spring 위임)→rerank→목록 push(경로 B)
            - cart: (상품/옵션/수량) intent → spring_client.add_to_cart(I-2 단건, 반복 호출) 위임
                    + 옵션 되물음 멀티턴(CART_OPTION_REQUIRED) + get_cart(I-18) 조회 응답 (§4.1·§4.9)
            - fallback: 일반 대화 폴백

결정 4-A: 일시적/상황적 요청("이번엔 비싸도 돼")은 thread checkpointer/state(session_context)
에만 기록하고 세션 종료 시 폐기 — 장기 프로필 write 후보에서 제외한다.

TODO(SPEC-RECOMMEND-001): StateGraph 구성, 노드/엣지 정의, PostgresSaver checkpointer 연결.
"""

from __future__ import annotations


def build_buyer_graph():  # noqa: ANN201 - 스텁, 반환 타입은 그래프 컴파일 후 확정
    """구매자 그래프를 컴파일해 반환한다 (스텁).

    실제 구현 전까지 NotImplementedError. api/chat.py 는 이 그래프 대신 스텁 스트림을 쓴다.
    """
    raise NotImplementedError("buyer graph not implemented yet (SPEC-RECOMMEND-001)")
