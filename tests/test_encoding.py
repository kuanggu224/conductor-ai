"""Encoding bootstrap tests."""

from conductor.io.encoding import UTF8_CODE_PAGE, configure_utf8_stdio, looks_like_mojibake, utf8_subprocess_environment


def test_configure_utf8_stdio_is_safe_to_call_repeatedly() -> None:
    configure_utf8_stdio()
    configure_utf8_stdio()

    assert UTF8_CODE_PAGE == 65001


def test_utf8_subprocess_environment_sets_child_encoding_defaults() -> None:
    environment = utf8_subprocess_environment({"CUSTOM": "value"})

    assert environment["PYTHONUTF8"] == "1"
    assert environment["PYTHONIOENCODING"] == "utf-8"
    assert environment["CUSTOM"] == "value"


def test_looks_like_mojibake_detects_corrupted_chinese_without_flagging_valid_text() -> None:
    assert looks_like_mojibake("本地读书清单，支持筛选和导出 CSV") is False
    assert looks_like_mojibake("鏈湴璇讳功娓呭崟 绛涢€夌姸鎬") is True
