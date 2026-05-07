"""Encoding bootstrap tests."""

from conductor.io.encoding import UTF8_CODE_PAGE, configure_utf8_stdio


def test_configure_utf8_stdio_is_safe_to_call_repeatedly() -> None:
    configure_utf8_stdio()
    configure_utf8_stdio()

    assert UTF8_CODE_PAGE == 65001
