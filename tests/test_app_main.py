"""入口参数测试。"""

from app.main import parse_requirement


def test_parse_requirement_supports_natural_language_cli_input() -> None:
    requirement = parse_requirement(["app/main.py", "实现一个", "包含", "API", "和测试", "的功能"])

    assert requirement == "实现一个 包含 API 和测试 的功能"
