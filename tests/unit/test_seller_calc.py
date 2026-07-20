"""app/agents/seller/calc.py 순수 함수 테스트 (DESIGN-SELLER-TOOLS-STAGE1 §6).

전부 stdlib 만으로 실행 가능 — 결정론(같은 입력 = 같은 출력)과 임계값 주입을 검증한다.
"""

from __future__ import annotations

import datetime as dt

import pytest

from app.agents.seller import calc
from app.schemas.spring import FunnelResult, SalesSeriesPoint


def test_moving_average_window_boundary() -> None:
    """len < window 구간은 None, 이후는 정확한 평균값."""
    values = [10.0, 20.0, 30.0, 40.0, 50.0]
    result = calc.moving_average(values, window=3)
    assert result[0] is None
    assert result[1] is None
    assert result[2] == 20.0  # (10+20+30)/3
    assert result[3] == 30.0  # (20+30+40)/3
    assert result[4] == 40.0  # (30+40+50)/3


def test_deviation_pct_sign_and_zero_baseline() -> None:
    """양/음 부호가 실측-기준 방향과 일치하고, baseline==0 이면 0.0."""
    assert calc.deviation_pct(120.0, 100.0) == 20.0
    assert calc.deviation_pct(80.0, 100.0) == -20.0
    assert calc.deviation_pct(50.0, 0.0) == 0.0


def test_is_anomaly_threshold_boundary() -> None:
    """편차 절대값이 임계값과 같으면 이상(True), 미만이면 False."""
    assert calc.is_anomaly(30.0, threshold_pct=30.0) is True
    assert calc.is_anomaly(-30.0, threshold_pct=30.0) is True
    assert calc.is_anomaly(29.9, threshold_pct=30.0) is False


def test_detect_sales_anomalies_ignores_spring_flags() -> None:
    """Spring 이 준 isAnomaly 가 반대여도 원시 sales 로 재판정한다(§0.1 D)."""
    series = [
        SalesSeriesPoint(
            date="2026-07-01", sales=100, order_count=10, is_anomaly=True, deviation_pct=999.0
        ),
        SalesSeriesPoint(
            date="2026-07-02", sales=100, order_count=10, is_anomaly=True, deviation_pct=999.0
        ),
        SalesSeriesPoint(
            date="2026-07-03", sales=100, order_count=10, is_anomaly=True, deviation_pct=999.0
        ),
        # 이동평균(100) 대비 300% 급증 — 실제로 이상이어야 함.
        SalesSeriesPoint(
            date="2026-07-04", sales=400, order_count=40, is_anomaly=False, deviation_pct=0.0
        ),
    ]
    results = calc.detect_sales_anomalies(series, window=3, threshold_pct=30.0)
    dates = [r[0] for r in results]
    assert dates == ["2026-07-01", "2026-07-02", "2026-07-03", "2026-07-04"]
    # 경계 구간(직전 window 일 데이터 부족)은 판정 보류.
    assert results[0][2] is False
    assert results[1][2] is False
    assert results[2][2] is False
    # 4번째: 직전 3일(100,100,100) 평균=100 대비 actual=400 → deviation=300% → 이상.
    assert results[3][1] == 300.0
    assert results[3][2] is True


def test_conversion_rates_and_drop() -> None:
    """단계 전환율 계산과 baseline 대비 하락 임계 판정."""
    current = FunnelResult(view=1000, cart=100, checkout=50, purchase=40)
    rates = calc.conversion_rates(current)
    assert rates["view_to_cart"] == 10.0
    assert rates["cart_to_checkout"] == 50.0
    assert rates["checkout_to_purchase"] == 80.0

    baseline = FunnelResult(view=1000, cart=200, checkout=100, purchase=90)
    drop = calc.compare_conversion(current, baseline, drop_pct=20.0)
    # view_to_cart: baseline 20% → current 10% → -50% 하락 → 이상.
    assert drop["view_to_cart"] is True
    # cart_to_checkout: baseline 50% → current 50% → 하락 없음.
    assert drop["cart_to_checkout"] is False


def test_compare_conversion_baseline_zero_no_drop() -> None:
    """baseline 전환율이 0(분모 0)이면 비교 기준이 없어 하락으로 판정하지 않는다(opus 리뷰 m6)."""
    # baseline.cart == 0 → cart_to_checkout 의 baseline 전환율(=checkout/cart)이 0.
    baseline = FunnelResult(view=1000, cart=0, checkout=0, purchase=0)
    current = FunnelResult(view=1000, cart=100, checkout=50, purchase=40)

    drop = calc.compare_conversion(current, baseline, drop_pct=20.0)

    assert drop["view_to_cart"] is False
    assert drop["cart_to_checkout"] is False
    assert drop["checkout_to_purchase"] is False


def test_normalize_period_last_month_year_rollover() -> None:
    """1월 today → 전년 12/1~12/31 로 롤오버한다."""
    today = dt.date(2026, 1, 15)
    start, end = calc.normalize_period("지난달", today=today, recent_default_days=7)
    assert start == dt.date(2025, 12, 1)
    assert end == dt.date(2025, 12, 31)


def test_normalize_period_recent_n_excludes_today() -> None:
    """ "최근 N일"은 (today-N)~(today-1) — 오늘은 포함하지 않는다."""
    today = dt.date(2026, 7, 17)
    start, end = calc.normalize_period("최근 7일", today=today, recent_default_days=3)
    assert start == dt.date(2026, 7, 10)
    assert end == dt.date(2026, 7, 16)

    # N 미지정("최근") 이면 recent_default_days 사용.
    start2, end2 = calc.normalize_period("최근", today=today, recent_default_days=3)
    assert start2 == dt.date(2026, 7, 14)
    assert end2 == dt.date(2026, 7, 16)


def test_normalize_period_explicit_range() -> None:
    """"YYYY-MM-DD~YYYY-MM-DD" 명시 범위는 그대로 반환한다(3-1 확장, 공백 허용)."""
    today = dt.date(2026, 7, 18)
    start, end = calc.normalize_period(
        "2026-06-01~2026-06-15", today=today, recent_default_days=7
    )
    assert start == dt.date(2026, 6, 1)
    assert end == dt.date(2026, 6, 15)

    start2, end2 = calc.normalize_period(
        "2026-06-01 ~ 2026-06-15", today=today, recent_default_days=7
    )
    assert (start2, end2) == (start, end)


def test_normalize_period_explicit_range_rejects_invalid() -> None:
    """명시 범위의 역전(from>to)·달력에 없는 날짜는 ValueError(되묻기 경로)."""
    today = dt.date(2026, 7, 18)
    with pytest.raises(ValueError):
        calc.normalize_period("2026-06-15~2026-06-01", today=today, recent_default_days=7)
    with pytest.raises(ValueError):
        calc.normalize_period("2026-02-30~2026-03-01", today=today, recent_default_days=7)


def test_normalize_period_recent_nonpositive_days_raises() -> None:
    """"최근 0일" 등 N≤0 은 역전 범위(from>to)가 되므로 ValueError(마감 리뷰 M3)."""
    today = dt.date(2026, 7, 18)
    with pytest.raises(ValueError):
        calc.normalize_period("최근 0일", today=today, recent_default_days=7)
    with pytest.raises(ValueError):
        calc.normalize_period("최근", today=today, recent_default_days=0)  # 설정 오류 방어


def test_normalize_period_unsupported_expr_raises() -> None:
    """미지원 표현("이번 달" 등)은 ValueError — 되묻기로 처리한다(2026-07-18 확정)."""
    today = dt.date(2026, 7, 18)
    for expr in ("이번 달", "이번달", "올해", "작년 여름"):
        with pytest.raises(ValueError):
            calc.normalize_period(expr, today=today, recent_default_days=7)


def test_safe_eval_basic_arithmetic() -> None:
    """사칙연산·거듭제곱·round() 는 허용된다 (calculate 도구 기반)."""
    assert calc.safe_eval("1200000 / 45 * 100") == 1200000 / 45 * 100
    assert calc.safe_eval("round(1234.5678, 2)") == 1234.57
    assert calc.safe_eval("2 ** 10") == 1024


def test_safe_eval_blocks_import_attribute_and_names() -> None:
    """__import__·속성 접근·변수 참조는 전부 ValueError 로 차단된다(보안, LLM 임의 코드 방지)."""
    with pytest.raises(ValueError):
        calc.safe_eval("__import__('os').system('ls')")
    with pytest.raises(ValueError):
        calc.safe_eval("(1).__class__")
    with pytest.raises(ValueError):
        calc.safe_eval("x + 1")


def test_calc_uses_injected_thresholds() -> None:
    """다른 임계값을 주입하면 결과가 달라진다(하드코딩 부재 확인)."""
    series = [
        SalesSeriesPoint(date="2026-07-01", sales=100, order_count=10),
        SalesSeriesPoint(date="2026-07-02", sales=100, order_count=10),
        SalesSeriesPoint(date="2026-07-03", sales=115, order_count=11),
    ]
    strict = calc.detect_sales_anomalies(series, window=2, threshold_pct=10.0)
    lenient = calc.detect_sales_anomalies(series, window=2, threshold_pct=50.0)
    # 동일 데이터, 다른 threshold_pct → 이상 판정이 달라져야 한다.
    assert strict[2][2] is True
    assert lenient[2][2] is False


def test_safe_eval_rejects_giant_power_dos() -> None:
    """LLM 생성식의 거대 거듭제곱은 평가 전에 ValueError 로 차단한다(DoS 방어, 리뷰 반영)."""
    for expr in ("9**9**9**9", "10**9999999", "2**1000000"):
        with pytest.raises(ValueError):
            calc.safe_eval(expr)


def test_safe_eval_allows_normal_power() -> None:
    """정상 범위 거듭제곱은 그대로 평가된다(가드 오탐 없음)."""
    assert calc.safe_eval("2**10") == 1024
    assert calc.safe_eval("1000**3") == 1_000_000_000
