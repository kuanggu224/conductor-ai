"""Requirement input loading tests."""

import json

import pytest

from conductor.io.requirements import RequirementInputError, load_requirement_text


def test_load_requirement_from_utf8_text_file(tmp_path) -> None:
    path = tmp_path / "requirement.txt"
    path.write_text("实现中文需求：费用报销和审批", encoding="utf-8")

    assert load_requirement_text(requirement_file=path) == "实现中文需求：费用报销和审批"


def test_load_requirement_from_utf8_json_file(tmp_path) -> None:
    path = tmp_path / "requirement.json"
    path.write_text(
        json.dumps({"task": {"prompt": "实现中文需求：限流配置中心"}}, ensure_ascii=False),
        encoding="utf-8",
    )

    assert load_requirement_text(requirement_json_file=path, json_key="task.prompt") == "实现中文需求：限流配置中心"


def test_load_requirement_rejects_empty_input() -> None:
    with pytest.raises(RequirementInputError):
        load_requirement_text("  ")
