from greeting import render_greeting


def test_render_greeting_adds_comma_and_exclamation() -> None:
    assert render_greeting("Jarvis") == "Hello, Jarvis!"
