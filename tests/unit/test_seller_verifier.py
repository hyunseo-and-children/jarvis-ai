"""보고서 결정론 검증 테스트 (SPEC-SELLER-001 §10-⑦ 전반부 — 순수 함수, LLM 없음)."""

from __future__ import annotations

from app.agents.seller.schemas import AnalysisFinding
from app.agents.seller.verifier import (
    DETERMINISTIC_CHECKS,
    run_deterministic_checks,
)


def _finding(**overrides) -> AnalysisFinding:
    base = {
        "analysis_type": "sales_anomaly",
        "summary": "6월 12일 매출이 직전 7일 평균 대비 42.1% 급락했다.",
        "evidence": ["06-12 매출 180,000원 (직전 7일 평균 310,000원)"],
        "severity": "warning",
    }
    base.update(overrides)
    return AnalysisFinding(**base)


def test_pass_when_numbers_grounded() -> None:
    """finding 수치만 인용한 정상 보고서는 전체 검사를 통과한다(빈 리스트)."""
    report = (
        "6월 12일 매출이 180,000원으로 직전 7일 평균 310,000원 대비 "
        "42.1% 급락했습니다. 원인 점검을 권장드립니다."
    )
    assert run_deterministic_checks(report, [_finding()]) == []


def test_d1_empty_report_fails() -> None:
    """D1 — 공백뿐인 보고서는 실패한다."""
    reasons = run_deterministic_checks("   \n  ", [_finding()])
    assert any("비어" in r for r in reasons)


def test_d2_novel_number_fails() -> None:
    """D2 — finding 에 없는 수치(환각)를 인용하면 해당 숫자가 사유에 나열된다."""
    report = "매출이 999,999원으로 급락했습니다. 180,000원 대비 심각합니다."
    reasons = run_deterministic_checks(report, [_finding()])
    assert len(reasons) == 1
    assert "999999" in reasons[0]
    assert "180000" not in reasons[0]  # 근거 있는 수치는 통과


def test_d2_small_numbers_are_tolerated() -> None:
    """D2 과탐 완화 — 2자리 이하 숫자("3일 연속" 등)는 근거 대조에서 제외된다."""
    report = "최근 3일 연속 하락했고 06-12 에 180,000원까지 내려갔습니다."
    assert run_deterministic_checks(report, [_finding()]) == []


def test_d3_degrade_must_be_disclosed() -> None:
    """D3 — 확보 실패 finding 이 있으면 보고서가 한계를 명시해야 한다."""
    degraded = _finding(
        analysis_type="abuse",
        summary="데이터 확보 실패 — I-13 조회가 타임아웃되어 분석을 생략했다.",
        evidence=[],
        severity="info",
    )
    hiding = "매출 분석 결과 특이사항이 없습니다."
    reasons = run_deterministic_checks(hiding, [degraded])
    assert any("한계" in r for r in reasons)

    honest = "일부 데이터 확보 실패로 어뷰징 분석은 제외됐습니다."
    assert run_deterministic_checks(honest, [degraded]) == []


def test_d2_dates_are_masked_not_flagged() -> None:
    """R1(3-4) — 연도 계열 날짜 표기(ISO·연-월·N년)는 근거 없는 수치로 오탐하지 않는다."""
    report = (
        "2026-06-01~2026-06-30 기간 분석입니다. 2026년 6월 12일 매출이 "
        "180,000원으로 직전 7일 평균 310,000원 대비 42.1% 급락했습니다."
    )
    assert run_deterministic_checks(report, [_finding()]) == []


def test_d2_still_catches_four_digit_hallucination() -> None:
    """R1 마스킹은 날짜 표기만 — 날짜가 아닌 4자리 환각 수치(2026원 등)는 여전히 잡는다."""
    report = "매출이 2026원으로 떨어졌고 평균은 310,000원입니다."
    reasons = run_deterministic_checks(report, [_finding()])
    assert len(reasons) == 1
    assert "2026" in reasons[0]


def test_d3_structural_detection_survives_rewording() -> None:
    """R2(3-4) — degrade 판정은 구조(severity=info+빈 evidence) — 워커가 '확보 실패'
    문구를 안 써도 은폐를 잡는다(문자열 의존 제거)."""
    reworded = _finding(
        analysis_type="abuse",
        summary="조회가 원활하지 않아 이번에는 어뷰징 분석을 건너뛰었다.",  # 규약 문구 이탈
        evidence=[],
        severity="info",
    )
    hiding = "매출 분석 결과 특이사항이 없습니다. 180,000원과 310,000원, 42.1% 참조."
    reasons = run_deterministic_checks(hiding, [reworded])
    assert any("한계" in r for r in reasons)


def test_d3_info_with_evidence_is_not_degrade() -> None:
    """정상 '이상 없음' finding(info + evidence 있음)은 degrade 로 오인하지 않는다."""
    calm = _finding(
        summary="이상 신호가 없다.",
        evidence=["기간 매출 합계 1,200,000원"],
        severity="info",
    )
    report = "기간 매출 합계는 1,200,000원이며 특이사항이 없습니다."
    assert run_deterministic_checks(report, [calm]) == []


def test_registry_names_unique() -> None:
    """레지스트리 체크 이름은 유일해야 한다 — 로그·디버깅 식별자."""
    names = [name for name, _fn in DETERMINISTIC_CHECKS]
    assert len(names) == len(set(names))
    assert names == ["not_empty", "numbers_grounded", "degrade_disclosed"]
