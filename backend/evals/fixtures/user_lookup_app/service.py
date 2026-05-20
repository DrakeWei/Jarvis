from users import normalize_email


def lookup_key(email: str) -> str:
    return normalize_email(email)
