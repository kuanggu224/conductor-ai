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


def test_looks_like_mojibake_detects_common_cp936_ui_label_corruption() -> None:
    assert looks_like_mojibake("阅读清单，我的阅读清单，输入书名，添加，导出书单") is False
    assert looks_like_mojibake("\u95c3\u5470\ue1f0\u5a13\u547d\u5d1f") is True
    assert looks_like_mojibake("\u6748\u64b3\u53c6\u6d94\ufe40\u6095") is True
    assert looks_like_mojibake("\u7035\u714e\u56ad\u6d94\ufe40\u5d1f") is True
