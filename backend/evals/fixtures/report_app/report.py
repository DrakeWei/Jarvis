from formatter import headline


def build_report(title: str, body: str) -> str:
    return f"{headline(title)}\n\n{body}"
