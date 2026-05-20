from service import user_cache_key


def test_user_cache_key_uses_uppercase_prefix() -> None:
    assert user_cache_key("42") == "USER:42"
