from cache import cache_key


def user_cache_key(user_id: str) -> str:
    return cache_key("user", user_id)
