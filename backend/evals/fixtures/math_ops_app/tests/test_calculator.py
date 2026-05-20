from calculator import area


def test_area_uses_multiplication() -> None:
    assert area(4, 3) == 12
