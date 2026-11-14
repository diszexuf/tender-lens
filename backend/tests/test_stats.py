from decimal import Decimal as D

import pytest

from app.calc.stats import (
    CalcRules,
    OutlierMethod,
    PositionStatus,
    PriceItem,
    calculate_position,
    cluster_hints,
    describe,
    find_outliers,
    quantile,
)
from app.domain.reasons import Flag, Reason


def items(*prices: str) -> list[PriceItem]:
    return [PriceItem(f"o{i}", D(p)) for i, p in enumerate(prices)]


def test_describe_matches_manual_calculation() -> None:
    # Пример из UC-01: 7 цен
    s = describe([D(x) for x in ("1150", "1290", "1380", "1470", "1590", "1700", "1890")])
    assert s.n == 7
    assert s.mean.quantize(D("0.01")) == D("1495.71")
    assert s.median == D("1470")
    # σ с делителем n − 1 (STDEV.S в Excel)
    assert s.stdev is not None and s.stdev.quantize(D("0.01")) == D("252.31")
    assert s.cv is not None and s.cv.quantize(D("0.01")) == D("16.87")


def test_describe_single_value_has_no_cv() -> None:
    s = describe([D("100")])
    assert s.stdev is None and s.cv is None


def test_quantile_matches_excel_quartile_inc() -> None:
    values = [D(x) for x in (1, 2, 3, 4, 5, 6, 7, 8)]
    assert quantile(values, D("0.25")) == D("2.75")
    assert quantile(values, D("0.75")) == D("6.25")


def test_mad_excludes_obvious_outlier() -> None:
    keep, out, bounds = find_outliers(items("100", "102", "98", "101", "99", "500"), CalcRules())
    assert [o.offer_id for o in out] == ["o5"]
    assert out[0].reason == Reason.OUTLIER_MAD
    assert len(keep) == 5
    assert bounds is not None and bounds[0] < D("98") and bounds[1] < D("500")


def test_mad_zero_skips_filter() -> None:
    keep, out, bounds = find_outliers(items("100", "100", "100", "100", "300"), CalcRules())
    assert out == [] and bounds is None and len(keep) == 5


def test_iqr_rule() -> None:
    rules = CalcRules(outlier_method=OutlierMethod.IQR)
    _, out, _ = find_outliers(items("10", "11", "12", "13", "14", "40"), rules)
    assert [o.offer_id for o in out] == ["o5"]
    assert out[0].reason == Reason.OUTLIER_IQR


def test_ok_position() -> None:
    result = calculate_position(items("100", "110", "105", "95", "102"), D("10"), CalcRules())
    assert result.status == PositionStatus.OK
    assert result.unit_price == D("102.40")
    assert result.total_price == D("1024.00")
    assert result.excluded == []


def test_insufficient_data_returns_no_price() -> None:
    result = calculate_position(items("100", "110"), D("1"), CalcRules())
    assert result.status == PositionStatus.INSUFFICIENT_DATA
    assert result.unit_price is None and result.total_price is None
    assert result.hints


def test_small_sample_skips_outlier_filter() -> None:
    result = calculate_position(items("100", "105", "300", "102"), D("1"), CalcRules())
    assert Flag.OUTLIER_FILTER_SKIPPED_SMALL_N in result.flags
    assert result.excluded == []


def test_outlier_rule_does_not_cut_whole_cluster() -> None:
    """MAD «видит» 3 выброса из 7 — это не выбросы, а вторая группа цен."""
    result = calculate_position(
        items("900", "1000", "1100", "1200", "3800", "4200", "4500"), D("1"), CalcRules()
    )
    assert result.excluded == []
    assert any("неоднородность" in line for line in result.log)


def test_heterogeneous_sample_has_no_price_and_cluster_hint() -> None:
    result = calculate_position(
        items("900", "1000", "1100", "1200", "3800", "4200", "4500"), D("1"), CalcRules()
    )
    assert result.status == PositionStatus.HETEROGENEOUS
    assert result.unit_price is None
    assert any("две группы" in h for h in result.hints)


def test_trimming_is_limited_and_marked() -> None:
    rules = CalcRules(trimming_enabled=True, outlier_method=OutlierMethod.NONE)
    prices = ("100", "101", "102", "103", "104", "105", "106", "107", "108", "300")
    result = calculate_position(items(*prices), D("1"), rules)
    assert result.status == PositionStatus.OK_ADJUSTED
    assert [e.reason for e in result.excluded] == [Reason.CV_TRIMMING]
    assert len(result.excluded) <= int(len(prices) * rules.trimming_max_share)


def test_trimming_cannot_fix_spread_stays_heterogeneous() -> None:
    rules = CalcRules(trimming_enabled=True, outlier_method=OutlierMethod.NONE)
    result = calculate_position(items("10", "50", "100", "200", "400"), D("1"), rules)
    assert result.status == PositionStatus.HETEROGENEOUS
    assert len(result.excluded) <= 1


def test_rounding_half_up_to_kopecks() -> None:
    result = calculate_position(items("10.005", "10.005", "10.005"), D("3"), CalcRules())
    assert result.unit_price == D("10.01")
    assert result.total_price == D("30.03")


@pytest.mark.parametrize(
    "prices",
    [("100", "110", "120", "130"), ("100", "101")],
)
def test_no_cluster_hint_for_uniform_or_tiny(prices: tuple[str, ...]) -> None:
    assert cluster_hints([D(p) for p in prices]) == []
