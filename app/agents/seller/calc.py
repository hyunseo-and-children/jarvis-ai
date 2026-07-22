"""판매자 분석 순수 함수 (DESIGN-SELLER-TOOLS-STAGE1 §4, SPEC-SELLER-001 §5).

3층 분담("Spring 원시 집계 → AI 고도화 계산(본 모듈) → LLM 자연어화") 중 계산 층.
stdlib `statistics`만 사용한다(pandas 미설치, §0.1 C) — 부작용 없는 순수 함수로만 구성해
같은 입력이면 같은 출력을 보장한다(결정론, §10-②).

[중요] Spring 이 준 SalesSeriesPoint.isAnomaly/deviationPct 는 참고치일 뿐이며, 본 모듈은
원시 sales 값으로 이동평균·편차를 직접 재계산해 판정한다(§0.1 D, C-13 경계 미확정 대비).

임계값(window·threshold_pct·drop_pct 등)은 전부 호출부가 app.core.config.Settings 에서
읽어 인자로 주입한다 — 이 파일 내부에 튜너블 숫자를 하드코딩하지 않는다.
"""

from __future__ import annotations

import ast
import math
import re
import statistics
from datetime import date, timedelta

from app.core.config import get_settings
from app.schemas.spring import FunnelResult, SalesSeriesPoint

# "최근 N일" 표현에서 N 을 추출하는 패턴 (normalize_period).
_RECENT_N_PATTERN = re.compile(r"최근\s*(\d+)\s*일")

# 명시 날짜 범위 "YYYY-MM-DD~YYYY-MM-DD" 패턴 (normalize_period, 3-1 확장).
_EXPLICIT_RANGE_PATTERN = re.compile(r"^(\d{4}-\d{2}-\d{2})\s*~\s*(\d{4}-\d{2}-\d{2})$")

# safe_eval 화이트리스트 — 사칙연산·거듭제곱·round() 만 허용한다(§3.3, `calculate` 도구용).
_ALLOWED_BINOPS = (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.FloorDiv, ast.Mod, ast.Pow)
_ALLOWED_UNARYOPS = (ast.UAdd, ast.USub)
_ALLOWED_FUNCS = {"round": round}


def moving_average(values: list[float], window: int) -> list[float | None]:
    """단순 이동평균(SMA). window 미만 구간(경계)은 None 으로 채운다.

    window <= 0 이면 ValueError(호출부 설정 오류 방어).
    """
    if window <= 0:
        raise ValueError(f"window 는 1 이상이어야 한다: {window}")

    result: list[float | None] = []
    for i in range(len(values)):
        if i < window - 1:
            result.append(None)
            continue
        segment = values[i - window + 1 : i + 1]
        result.append(statistics.fmean(segment))
    return result


def deviation_pct(actual: float, baseline: float) -> float:
    """기준(baseline) 대비 실측(actual)의 편차 %. baseline==0 이면 0 나눗셈 방지로 0.0."""
    if baseline == 0:
        return 0.0
    return (actual - baseline) / baseline * 100


def is_anomaly(deviation: float, *, threshold_pct: float) -> bool:
    """|deviation| >= threshold_pct 면 이상. 경계(==)도 이상으로 판정한다."""
    return abs(deviation) >= threshold_pct


def detect_sales_anomalies(
    series: list[SalesSeriesPoint], *, window: int, threshold_pct: float
) -> list[tuple[str, float, bool]]:
    """일별 매출을 "직전 window 일 이동평균" 대비 편차·이상판정한다.

    (date, deviationPct, isAnomaly) 목록 반환. 당일 값은 자신의 기준(baseline) 계산에
    포함하지 않는다 — 급증/급락일이 스스로를 평균에 섞어 편차를 희석하는 것을 방지한다.
    moving_average(values, window)[i-1] 이 곧 "i일 직전 window 일 평균"이라는 성질을 이용한다.

    Spring 이 준 point.is_anomaly/point.deviation_pct 는 무시하고 point.sales 원시값만으로
    재계산한다(§0.1 D) — 경계표가 확정되기 전까지 AI 판정을 신뢰 원천으로 둔다.
    """
    values = [point.sales for point in series]
    trailing_averages = moving_average(values, window)

    results: list[tuple[str, float, bool]] = []
    for i, point in enumerate(series):
        # i-1 위치의 이동평균 = i 이전 window 일 평균(당일 미포함). 초반 경계는 baseline 없음.
        baseline = trailing_averages[i - 1] if i > 0 else None
        if baseline is None:
            results.append((point.date, 0.0, False))
            continue
        deviation = deviation_pct(point.sales, baseline)
        results.append((point.date, deviation, is_anomaly(deviation, threshold_pct=threshold_pct)))
    return results


def _safe_ratio_pct(numerator: int, denominator: int) -> float:
    """분모 0 이면 0.0 (0 나눗셈 방지) — 전환율 계산 내부 헬퍼."""
    if denominator == 0:
        return 0.0
    return numerator / denominator * 100


def conversion_rates(funnel: FunnelResult) -> dict[str, float]:
    """구매전환 퍼널 단계별 전환율(%) — view→cart→checkout→purchase."""
    return {
        "view_to_cart": _safe_ratio_pct(funnel.cart, funnel.view),
        "cart_to_checkout": _safe_ratio_pct(funnel.checkout, funnel.cart),
        "checkout_to_purchase": _safe_ratio_pct(funnel.purchase, funnel.checkout),
    }


def compare_conversion(
    current: FunnelResult, baseline: FunnelResult, *, drop_pct: float
) -> dict[str, bool]:
    """단계별 전환율이 baseline 대비 drop_pct 이상 하락했는지 판정한다.

    baseline 전환율이 0 이면 비교 기준이 없어 하락 판정을 내리지 않는다(False).
    """
    current_rates = conversion_rates(current)
    baseline_rates = conversion_rates(baseline)

    result: dict[str, bool] = {}
    for stage, base_rate in baseline_rates.items():
        if base_rate == 0:
            result[stage] = False
            continue
        deviation = deviation_pct(current_rates[stage], base_rate)
        result[stage] = deviation <= -drop_pct
    return result


def normalize_period(expr: str, *, today: date, recent_default_days: int) -> tuple[date, date]:
    """자연어 기간 표현 → (from, to) 날짜 범위.

    - "지난달": 전월 1일 ~ 전월 말일(연 경계 롤오버 처리 — 1월이면 전년 12월).
    - "최근 N일" / "최근": (today - N) ~ (today - 1). 오늘은 항상 제외한다
      (§10-④, 당일 데이터는 아직 집계가 완결되지 않았을 수 있어 경계에서 뺀다).
      N 이 명시되지 않으면 recent_default_days 를 사용한다.
    - "어제": (today - 1) ~ (today - 1).
    - "YYYY-MM-DD~YYYY-MM-DD"(3-1 확장): 명시 범위 그대로. LLM 은 질문의 날짜를
      옮겨적기만 한다(날짜 산수 금지, 장치 ④). from > to 면 ValueError.
    - 파싱 불가 표현("이번 달" 포함, 2026-07-18 확정)은 ValueError
      (호출부가 사용자에게 되물어야 함을 알린다).
    """
    text = expr.strip()

    range_match = _EXPLICIT_RANGE_PATTERN.match(text)
    if range_match:
        try:
            start = date.fromisoformat(range_match.group(1))
            end = date.fromisoformat(range_match.group(2))
        except ValueError as exc:
            raise ValueError(f"유효하지 않은 날짜: {expr!r}") from exc
        if start > end:
            raise ValueError(f"기간 역전(from > to): {expr!r}")
        return start, end

    if text in ("지난달", "지난 달"):
        first_of_this_month = today.replace(day=1)
        last_day_prev_month = first_of_this_month - timedelta(days=1)
        first_day_prev_month = last_day_prev_month.replace(day=1)
        return first_day_prev_month, last_day_prev_month

    if "최근" in text:
        match = _RECENT_N_PATTERN.search(text)
        n = int(match.group(1)) if match else recent_default_days
        if n <= 0:
            # "최근 0일" 등 — 역전 범위(from>to)가 무음 통과하던 구멍(마감 리뷰 M3).
            raise ValueError(f"기간 일수가 유효하지 않다: {expr!r}")
        end = today - timedelta(days=1)
        start = today - timedelta(days=n)
        return start, end

    if text == "어제":
        yesterday = today - timedelta(days=1)
        return yesterday, yesterday

    raise ValueError(f"파싱 불가한 기간 표현: {expr!r}")


def safe_eval(expression: str) -> float:
    """LLM 이 만든 계산식을 안전하게 평가한다 (ast 화이트리스트, §3.3 `calculate` 도구용).

    허용: 숫자·괄호·사칙연산(+ - * / // % **)·단항 부호·`round()` 호출뿐이다.
    `__import__`·속성 접근(`a.b`)·변수 참조(`a`)·기타 함수 호출은 전부 ValueError 로 차단한다
    — LLM 이 생성한 임의 코드를 신뢰하지 않고 구조적으로 막는다(보안).
    """
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise ValueError(f"계산식을 파싱할 수 없습니다: {expression!r}") from exc
    return _safe_eval_node(tree.body)


def _safe_eval_node(node: ast.AST) -> float:
    """safe_eval 내부 재귀 평가기 — 화이트리스트 밖 노드는 전부 ValueError."""
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or not isinstance(node.value, int | float):
            raise ValueError(f"허용되지 않는 상수: {node.value!r}")
        return node.value

    if isinstance(node, ast.BinOp):
        if not isinstance(node.op, _ALLOWED_BINOPS):
            raise ValueError(f"허용되지 않는 연산자: {type(node.op).__name__}")
        left = _safe_eval_node(node.left)
        right = _safe_eval_node(node.right)
        return _apply_binop(node.op, left, right)

    if isinstance(node, ast.UnaryOp):
        if not isinstance(node.op, _ALLOWED_UNARYOPS):
            raise ValueError(f"허용되지 않는 단항 연산자: {type(node.op).__name__}")
        value = _safe_eval_node(node.operand)
        return value if isinstance(node.op, ast.UAdd) else -value

    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name) or node.func.id not in _ALLOWED_FUNCS:
            raise ValueError("허용되지 않는 함수 호출입니다(round() 만 허용)")
        if node.keywords:
            raise ValueError("키워드 인자는 허용되지 않습니다")
        args = [_safe_eval_node(arg) for arg in node.args]
        return _ALLOWED_FUNCS[node.func.id](*args)

    # Name(변수 참조)·Attribute(속성 접근)·Import 등은 여기서 전부 차단된다.
    raise ValueError(f"허용되지 않는 표현식 요소: {type(node).__name__}")


def _apply_binop(op: ast.operator, left: float, right: float) -> float:
    """BinOp 화이트리스트 연산 적용 — _safe_eval_node 헬퍼."""
    if isinstance(op, ast.Add):
        return left + right
    if isinstance(op, ast.Sub):
        return left - right
    if isinstance(op, ast.Mult):
        return left * right
    if isinstance(op, ast.Div):
        return left / right
    if isinstance(op, ast.FloorDiv):
        return left // right
    if isinstance(op, ast.Mod):
        return left % right
    if isinstance(op, ast.Pow):
        # DoS 방어(리뷰 반영): 동기 블로킹은 int**int(CPython 임의정밀도, 결과가 수백만
        # 자리로 커짐)에서만 발생한다 — 9**9**9**9·10**9999999 등이 이벤트 루프를 막아
        # 프로세스 공유 세션까지 정지시킨다. float 가 섞이면 C pow(O(1))라 블로킹이 없고
        # 과대 지수는 OverflowError 로 빠르게 종결되므로 가드 대상에서 제외한다(오탐 방지).
        if (
            isinstance(left, int)
            and isinstance(right, int)
            and right > 0
            and left not in (0, 1, -1)
        ):
            est_digits = right * math.log10(abs(left))
            max_digits = get_settings().seller_calc_max_result_digits
            if est_digits > max_digits:
                raise ValueError(
                    f"계산 결과가 너무 큽니다(약 {int(est_digits)}자리, 상한 {max_digits}자리)"
                )
        return left**right
    raise ValueError(f"허용되지 않는 연산자: {type(op).__name__}")
