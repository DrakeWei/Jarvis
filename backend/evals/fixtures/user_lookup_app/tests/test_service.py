from service import lookup_key


def test_lookup_key_lowercases_email() -> None:
    assert lookup_key("  USER@Example.com ") == "user@example.com"
