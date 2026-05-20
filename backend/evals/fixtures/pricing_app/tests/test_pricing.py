from pricing import total_after_discount


def test_loyalty_discount_applies_ten_percent() -> None:
    assert total_after_discount(200, 120) == 180


def test_loyalty_discount_not_applied_below_threshold() -> None:
    assert total_after_discount(200, 80) == 200
