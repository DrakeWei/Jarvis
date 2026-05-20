from settings import feature_enabled


def test_feature_enabled_accepts_false_values() -> None:
    assert feature_enabled("false") is False
    assert feature_enabled("0") is False


def test_feature_enabled_uses_default_when_missing() -> None:
    assert feature_enabled(None) is True
