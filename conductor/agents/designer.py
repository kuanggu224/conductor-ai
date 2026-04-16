"""Designer Agent - 负责项目设计阶段的工作项执行。"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from conductor.agents.agent import Agent
from conductor.agents.profile import AgentProfile
from conductor.domain.models import Capability, WorkItem

if TYPE_CHECKING:
    from conductor.context.models import ContextPack


@dataclass(slots=True)
class DesignerAgent(Agent):
    """
    设计师 Agent，负责：
    1. 把自然语言需求转成结构化设计文档
    2. 协作评审后统一修订设计
    3. 产出设计概述、UI设计、API设计、测试设计等
    """

    def __init__(
        self,
        profile: AgentProfile,
        llm_backend: "LLMBackend" | None = None,
    ) -> None:
        super().__init__(
            id="agent-designer",
            role=profile.role_name,
            profile=profile,
            capabilities=profile.capabilities,
            backend="llm",
            llm_backend=llm_backend,
            execution_backend="llm",
            preferred_llm_backend="cloud",
        )

    def execute(self, workitem: WorkItem) -> str:
        """
        执行设计类工作项，根据工作项类型生成相应的设计文档。

        Args:
            workitem: 要执行的工作项

        Returns:
            str: 设计结果
        """
        workitem_kind = workitem.kind or "design_overview"

        if workitem_kind == "design_overview":
            return self._generate_design_overview(workitem)
        elif workitem_kind == "ui_design":
            return self._generate_ui_design(workitem)
        elif workitem_kind == "api_design":
            return self._generate_api_design(workitem)
        elif workitem_kind == "test_design":
            return self._generate_test_design(workitem)
        else:
            return self._generate_generic_design(workitem)

    def _generate_design_overview(self, workitem: WorkItem) -> str:
        """生成设计概述文档。"""
        return (
            "## 设计概述\n"
            "\n### 工作项描述\n"
            f"{workitem.description}\n"
            "\n### 目标\n"
            "1. 明确需求边界\n"
            "2. 定义核心功能范围\n"
            "3. 识别技术风险\n"
            "4. 规划实施路径\n"
            "\n### 关键假设\n"
            "- 用户具备基本的技术使用能力\n"
            "- 系统将部署在标准服务器环境中\n"
            "- 数据存储将使用关系型数据库\n"
            "\n### 技术方案\n"
            "采用分层架构设计：\n"
            "- 前端：响应式 Web 界面\n"
            "- 后端：RESTful API 服务\n"
            "- 数据库：关系型数据库\n"
            "\n### 交付物\n"
            "- API 设计文档\n"
            "- UI 原型设计\n"
            "- 数据库设计\n"
            "- 测试计划\n"
            "\n### 风险与应对\n"
            "- 风险：需求变更可能影响进度\n"
            "  应对：采用敏捷开发方式，定期评审\n"
            "- 风险：技术选型可能不合适\n"
            "  应对：在实施前进行充分的技术验证\n"
        )

    def _generate_ui_design(self, workitem: WorkItem) -> str:
        """生成UI设计文档。"""
        return (
            "## UI 设计\n"
            "\n### 工作项描述\n"
            f"{workitem.description}\n"
            "\n### 页面结构\n"
            "1. **首页**：展示核心功能入口\n"
            "2. **列表页**：展示数据列表，支持搜索和筛选\n"
            "3. **详情页**：展示完整信息和操作选项\n"
            "4. **表单页**：用于数据录入和编辑\n"
            "\n### 组件设计\n"
            "- 导航栏：固定在顶部，包含品牌标识和主要功能菜单\n"
            "- 表格组件：用于展示列表数据，支持排序和分页\n"
            "- 表单组件：包含常用输入控件和验证逻辑\n"
            "- 模态框：用于展示详细信息或执行重要操作\n"
            "\n### 交互设计\n"
            "- 响应式设计：适配不同屏幕尺寸\n"
            "- 加载状态：所有异步操作显示加载动画\n"
            "- 错误提示：清晰展示错误信息和解决建议\n"
            "- 操作反馈：所有用户操作提供明确反馈\n"
            "\n### 技术选型\n"
            "- 框架：根据需求选择合适的前端框架\n"
            "- 样式库：使用现代 CSS 框架\n"
            "- 图标库：统一的图标风格\n"
        )

    def _generate_api_design(self, workitem: WorkItem) -> str:
        """生成API设计文档。"""
        return (
            "## API 设计\n"
            "\n### 工作项描述\n"
            f"{workitem.description}\n"
            "\n### 接口设计原则\n"
            "-  RESTful API 风格\n"
            "-  使用 JSON 数据格式\n"
            "-  统一的错误响应格式\n"
            "-  支持分页和筛选\n"
            "\n### 主要接口\n"
            "#### 查询接口\n"
            "```http\n"
            "GET /api/v1/items\n"
            "Accept: application/json\n"
            "\n"
            "响应：\n"
            "{\n"
            "  \"data\": [...],\n"
            "  \"pagination\": {\n"
            "    \"total\": 100,\n"
            "    \"page\": 1,\n"
            "    \"size\": 10\n"
            "  }\n"
            "}\n"
            "```\n"
            "\n#### 创建接口\n"
            "```http\n"
            "POST /api/v1/items\n"
            "Content-Type: application/json\n"
            "\n"
            "请求：\n"
            "{\n"
            "  \"name\": \"示例名称\",\n"
            "  \"description\": \"示例描述\"\n"
            "}\n"
            "\n"
            "响应：\n"
            "{\n"
            "  \"id\": \"1\",\n"
            "  \"name\": \"示例名称\",\n"
            "  \"description\": \"示例描述\",\n"
            "  \"created_at\": \"2024-01-01T00:00:00Z\"\n"
            "}\n"
            "```\n"
            "\n### 数据模型\n"
            "```json\n"
            "{\n"
            "  \"id\": \"string\",\n"
            "  \"name\": \"string\",\n"
            "  \"description\": \"string\",\n"
            "  \"status\": \"enum\",\n"
            "  \"created_at\": \"string\",\n"
            "  \"updated_at\": \"string\"\n"
            "}\n"
            "```\n"
        )

    def _generate_test_design(self, workitem: WorkItem) -> str:
        """生成测试设计文档。"""
        return (
            "## 测试设计\n"
            "\n### 工作项描述\n"
            f"{workitem.description}\n"
            "\n### 测试范围\n"
            "- 功能测试：验证核心业务功能\n"
            "- 接口测试：验证 API 接口的正确性\n"
            "- UI 测试：验证用户界面的交互\n"
            "- 性能测试：验证系统的响应速度\n"
            "- 安全测试：验证系统的安全性\n"
            "\n### 测试用例\n"
            "#### 功能测试\n"
            "- 测试场景：添加项目\n"
            "- 输入：项目名称、描述\n"
            "- 预期输出：项目成功创建，显示在列表中\n"
            "\n"
            "- 测试场景：删除项目\n"
            "- 输入：项目 ID\n"
            "- 预期输出：项目成功删除，从列表中移除\n"
            "\n#### 接口测试\n"
            "- 测试接口：GET /api/v1/items\n"
            "- 预期响应状态码：200 OK\n"
            "- 预期响应格式：JSON\n"
            "- 预期数据结构：包含 data 和 pagination 字段\n"
            "\n### 测试策略\n"
            "- 使用自动化测试工具执行接口和功能测试\n"
            "- 手动测试用户界面和交互逻辑\n"
            "- 集成测试验证系统各组件的协作\n"
            "- 性能测试使用压力测试工具\n"
            "\n### 验收标准\n"
            "1. 所有测试用例执行通过率达到 100%\n"
            "2. 系统响应时间在可接受范围内\n"
            "3. 系统在高负载下稳定运行\n"
            "4. 安全漏洞被识别并修复\n"
        )

    def _generate_generic_design(self, workitem: WorkItem) -> str:
        """生成通用设计文档。"""
        return (
            "## 设计文档\n"
            "\n### 工作项描述\n"
            f"{workitem.description}\n"
            "\n### 设计结果\n"
            "这是一个通用设计文档，根据需求自动生成。\n"
            "\n### 设计要点\n"
            "- 清晰的架构层次\n"
            "- 明确的接口边界\n"
            "- 完整的错误处理\n"
            "- 充分的测试覆盖\n"
            "\n### 下一步建议\n"
            "1. 对设计进行评审\n"
            "2. 根据评审意见修订设计\n"
            "3. 开始实施开发\n"
        )

    def think(self, prompt: str, context_pack: ContextPack | None = None, preferred_backend: str | None = None) -> str:
        """
        通过 LLM backend 执行一次推理。

        Args:
            prompt: 推理提示
            context_pack: 上下文包
            preferred_backend: 首选的后端

        Returns:
            str: 推理结果
        """
        if self.llm_backend is None:
            return super().think(prompt, context_pack, preferred_backend)

        from conductor.agents.llm import LLMRequest
        request = LLMRequest(
            user_prompt=prompt,
            context_pack=context_pack,
            preferred_backend=preferred_backend or self.preferred_llm_backend
        )
        response = self.llm_backend.generate(request)
        return response.content
