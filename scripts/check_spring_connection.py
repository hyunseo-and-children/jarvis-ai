#!/usr/bin/env python3
"""FastAPI 설정으로 Spring internal API 연결을 확인한다.

LLM 호출 없이 FastAPI가 실제로 사용하는 SPRING_BASE_URL과
INTERNAL_API_TOKEN을 사용해 자사 상품 목록 API를 읽기 전용으로 호출한다.
"""

from __future__ import annotations

import argparse
import asyncio

from app.core.config import get_settings
from app.services.spring_client import SpringUnavailableError, get_spring_client


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="FastAPI → Spring INTERNAL_API_TOKEN 연결을 확인합니다."
    )
    parser.add_argument(
        "--brand-id",
        default="7",
        help="조회할 판매자 브랜드 ID (기본값: 7)",
    )
    return parser.parse_args()


async def check_connection(brand_id: str) -> int:
    settings = get_settings()
    if not settings.internal_api_token:
        print("FAIL: FastAPI의 INTERNAL_API_TOKEN이 비어 있습니다.")
        return 1

    try:
        result = await get_spring_client().list_products(
            brand_id=brand_id,
            limit=1,
            offset=0,
        )
    except SpringUnavailableError as exc:
        print(f"FAIL: FastAPI → Spring 연결 실패: {exc}")
        return 1

    print(
        "OK: FastAPI → Spring 연결 성공 "
        f"(url={settings.spring_base_url}, brandId={brand_id}, returned={len(result.rows)})"
    )
    return 0


def main() -> int:
    args = parse_args()
    return asyncio.run(check_connection(args.brand_id))


if __name__ == "__main__":
    raise SystemExit(main())
