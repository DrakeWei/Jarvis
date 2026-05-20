from config import parse_bool


def feature_enabled(raw: str | None) -> bool:
    return parse_bool(raw, default=True)
