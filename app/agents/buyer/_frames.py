"""구매자 그래프 공용 SSE 프레임 직렬화.

각 이벤트는 SSE `data:` 라인에 {type, data} JSON 을 싣는다 (api-spec §3.1, camelCase).
api/chat.py `_sse` 와 동일 규약 — 그래프 노드가 공유한다.
"""

from __future__ import annotations

import json


def sse(event_type: str, data: dict) -> str:
    """SSE `data:` 프레임 1줄을 직렬화한다."""
    return f"data: {json.dumps({'type': event_type, 'data': data}, ensure_ascii=False)}\n\n"
