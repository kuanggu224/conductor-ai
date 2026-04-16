"""Conductor Sprint 1 示例入口。"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from conductor.controller.engine import ConductorEngine


def parse_requirement(argv: list[str]) -> str:
    """从命令行参数中读取自然语言需求。"""
    if len(argv) < 2:
        raise ValueError('请通过命令行传入需求，例如: python app/main.py "实现一个包含 API 和测试的功能"')
    return " ".join(argv[1:]).strip()


def main(argv: list[str] | None = None) -> int:
    """运行最小 mock 项目流程。"""
    args = argv if argv is not None else sys.argv
    try:
        requirement = parse_requirement(args)
    except ValueError as error:
        print(str(error))
        return 1

    engine = ConductorEngine()
    state = engine.create_project(requirement=requirement)
    print(f"项目已创建: {state.project.id}")
    print(f"项目目标: {state.project.goal}")
    print(f"可用角色: {', '.join(engine.registry.list_roles())}")
    print(f"规划角色: {', '.join(state.planned_roles)}")

    while not engine.is_terminal(state):
        print(f"当前阶段: {state.current_stage}")
        state = engine.step_project(state.project.id)

    print("流程日志:")
    for event in state.recent_events:
        print(f"- {event}")

    print("执行结果:")
    for execution in state.executions:
        print(f"- {execution.workitem_id} / {execution.agent_id} / {execution.status.value}")

    print("路由结果:")
    for decision in state.route_decisions:
        print(f"- {decision.workitem_id} -> {decision.selected_agent}")

    if state.blockers:
        print("阻塞项:")
        for blocker in state.blockers:
            print(f"- {blocker}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
