# Conductor Board 前端对接文档

## 1. 目的

这份文档给“重做前端页面”的实现者使用。

目标不是解释视觉风格，而是明确：

- 当前后端已经提供了哪些页面入口
- 页面依赖哪些数据块
- 哪些交互是必须保留的
- 实时运行输出如何接
- 哪些行为可以重构，哪些行为不能丢

当前系统是 **FastAPI + Jinja 模板**，但页面结构可以重做。
如果后续要改成 React/Vue/桌面 GUI，也建议保持下面的数据边界。


## 2. 当前页面入口

### 2.1 主看板

- `GET /`
- 用途：
  - 展示项目列表
  - 展示默认项目快照
  - 提供“创建项目”入口

模板上下文：

- `snapshot: BoardSnapshot | None`
- `available_roles: list[str]`
- `available_role_labels: list[str]`
- `requirement: str`
- `project_root: str`
- `project_summaries: list[BoardProjectSummary]`
- `task_status: ProjectTaskStatus | None`


### 2.2 创建项目

- `GET /projects/create`
- Query 参数：
  - `requirement: str`
  - `project_root: str | None`
- 行为：
  - 创建项目实例
  - 302/303 跳转到 `/projects/{project_id}`


### 2.3 项目详情页

- `GET /projects/{project_id}`
- 用途：
  - 展示指定项目的完整看板
  - 展示执行状态、会议桌、artifact、review、最近事件等

模板上下文与 `/` 基本一致，但 `snapshot` 是指定项目。


### 2.4 运行控制

- `GET /projects/{project_id}/step`
- `GET /projects/{project_id}/run`

行为：

- 后台线程启动任务
- 立即重定向回项目详情页
- 页面通过轮询/刷新/SSE 观察后续状态


### 2.5 实时流接口

- `GET /projects/{project_id}/runtime/stream`
- 类型：`text/event-stream`
- 用途：
  - 推送 CLI / Harness / Agent CLI 的实时输出尾流


### 2.6 目录选择页

- `GET /folders/pick`
- Query 参数：
  - `current_path`
  - `requirement`
- 用途：
  - 选择项目目录


### 2.7 设置页

- `GET /settings/execution`
- `POST /settings/execution`
- `GET /settings/cli`
- `POST /settings/cli`
- `GET /settings/llm`
- `POST /settings/llm`


## 3. 前端必须保留的核心区域

不管页面风格怎么重做，下列功能区建议保留：

1. 顶部项目创建区
- requirement 输入
- project_root 选择
- 创建项目动作

2. 项目列表区
- 最近项目
- 当前状态
- 当前阶段
- 进入项目详情

3. 项目主舞台
- 项目目标
- 项目目录
- 单步推进 / 运行到终态
- 当前运行状态

4. 需求/设计协作区
- 会议桌
- 当前需求文档
- review 内容
- 历史版本

5. 实时执行区
- 当前 Agent
- 当前执行后端
- 当前 CLI
- 当前模型
- 当前工作目录
- 当前执行模式
- CLI 实时输出

6. 产物区
- 需求/设计文档
- 协作评审文档
- 研发执行报告
- 其他 artifact

7. 过程可观测区
- WorkItems
- Route Decisions
- Gate History
- Recent Events
- Blockers
- 本项目 Agent 与激活原因


## 4. 核心数据模型

下面是前端最关心的数据结构。


### 4.1 BoardSnapshot

```ts
type BoardSnapshot = {
  project_id: string
  project_goal: string
  project_root: string
  project_status: string
  project_status_label: string
  current_stage: string
  current_stage_label: string

  planned_roles: string[]
  planned_role_labels: string[]

  blockers: string[]
  gate_history: string[]
  recent_events: string[]
  recent_events_tail: string[]
  route_lines: string[]

  workitems: BoardWorkItemView[]
  executions: BoardExecutionView[]
  artifacts: BoardArtifactView[]
  code_execution_artifacts: BoardArtifactView[]
  project_agents: BoardProjectAgentView[]

  execution_runtime: BoardExecutionRuntimeView
  design_collaboration: BoardDesignCollaborationView
}
```


### 4.2 BoardProjectSummary

```ts
type BoardProjectSummary = {
  project_id: string
  goal: string
  project_root: string
  status: string
  status_label: string
  current_stage: string
  current_stage_label: string
}
```


### 4.3 BoardWorkItemView

```ts
type BoardWorkItemView = {
  id: string
  stage: string
  stage_label: string
  kind: string
  kind_label: string
  status: string
  status_label: string
  owner_agent: string
  owner_agent_label: string
  description: string
  retry_text: string
}
```


### 4.4 BoardExecutionView

```ts
type BoardExecutionView = {
  workitem_id: string
  agent_id: string
  agent_label: string
  status: string
  status_label: string
  result: string
}
```


### 4.5 BoardArtifactView

```ts
type BoardArtifactView = {
  id: string
  title: string
  kind: string
  kind_label: string
  agent_id: string
  agent_label: string
  workitem_id: string
  content: string
  path: string
  source_backend: string
  source_backend_label: string
  version: number
  parent_artifact_id: string | null
  review_of: string | null
}
```

说明：

- `artifacts`：所有产物
- `code_execution_artifacts`：研发代码执行报告的子集


### 4.6 BoardProjectAgentView

```ts
type BoardProjectAgentView = {
  role: string
  role_label: string
  agent_id: string
  mission: string
  reason: string
  related_kinds: string[]
}
```

用途：

- 告诉前端“这个项目为什么激活了这些 Agent”


### 4.7 BoardExecutionRuntimeView

```ts
type BoardExecutionRuntimeView = {
  available: boolean
  is_running: boolean

  headline: string
  workitem_id: string
  workitem_kind_label: string
  stage_label: string

  agent_label: string
  backend_label: string
  cli_label: string
  model_label: string
  working_directory: string
  execution_mode_label: string
  state_label: string

  stage_progress_label: string
  stage_progress_percent: number
  task_position_label: string

  output_summary_title: string
  output_summary: string
}
```

用途：

- 当前执行上下文面板
- 如果没有运行中任务，则展示最近一次执行上下文


### 4.8 BoardDesignCollaborationView

```ts
type BoardDesignCollaborationView = {
  enabled: boolean
  current_step_label: string
  status_label: string
  current_document_title: string
  current_document_content: string
  current_document_source: string
  agents: BoardMeetingAgentView[]
  reviews: BoardReviewView[]
  history: BoardArtifactView[]
}
```


### 4.9 BoardMeetingAgentView

```ts
type BoardMeetingAgentView = {
  role: string
  label: string
  agent_id: string
  status: string
  status_label: string
}
```

典型状态：

- `active`
- `done`
- `waiting`


### 4.10 BoardReviewView

```ts
type BoardReviewView = {
  round_index: number
  role: string
  role_label: string
  decision: string
  decision_label: string
  content: string
}
```


### 4.11 ProjectTaskStatus

```ts
type ProjectTaskStatus = {
  running: boolean
  action: string
  action_label: string
  message: string
  error: string | null
}
```

用途：

- 控制“单步推进/运行到终态”按钮状态
- 给顶部状态提示条提供文案


## 5. 实时流接口

### 5.1 SSE 地址

```text
GET /projects/{project_id}/runtime/stream
```


### 5.2 返回格式

每条 SSE event 的 data 是 JSON：

```json
{
  "project_id": "project-xxxx",
  "running": true,
  "status": "running",
  "workitem_id": "workitem-001",
  "agent_role": "backend_engineer",
  "backend": "agent_cli/codex",
  "cli_name": "codex",
  "lines": [
    "collecting ...",
    "editing app.py ...",
    "pytest passed"
  ],
  "version": 12
}
```


### 5.3 前端处理建议

- 页面进入项目详情页后建立 `EventSource`
- 仅在当前项目页订阅对应 `project_id`
- `lines` 作为当前终端输出的最新缓冲区直接覆盖渲染
- 当 `running=false` 时：
  - 可以把状态切成“完成/失败”
  - 但不应立即清空日志窗口


## 6. 当前后端语义约束

这些语义建议前端不要改坏：

### 6.1 需求阶段是“会议桌”

需求/设计阶段不是普通卡片列表，语义上是：

- `designer` 出初稿
- `backend/frontend/tester` 串行 review
- `designer` 统一修订

因此该区域建议保持“协作桌面/战情室”风格，而不是普通表格。


### 6.2 研发执行区是“控制台”

研发执行不是单纯展示 artifact，而是：

- 当前执行上下文
- 当前执行模式
- 当前 CLI
- 当前模型
- 当前工作目录
- 实时输出流

这一区域适合做成终端/仪表盘样式。


### 6.3 产物区分两类

建议前端至少区分：

1. 需求/设计/协作文档
2. 研发代码执行报告

因为二者观看方式不同：

- 文档适合正文阅读
- 执行报告更适合查看 backend、改动文件、验证结果


## 7. 建议的前端布局

可以重做，但建议遵循这个信息架构：

### 顶部

- 品牌 / 系统名称
- 当前项目状态
- 全局操作入口
- 设置入口

### 左侧窄栏

- 项目列表
- 项目切换
- 创建项目入口

### 中间主舞台

- 项目目标
- 会议桌
- 当前需求文档
- 研发执行报告

### 右侧情报栏

- 当前执行上下文
- CLI 实时输出
- Route
- Gate
- Recent Events
- Blockers
- Project Agents


## 8. 前端重做时不能丢的功能

1. 创建项目
2. 选择项目目录
3. 查看项目列表
4. 查看项目详情
5. 单步推进
6. 运行到终态
7. 查看需求协作内容
8. 查看 artifact
9. 查看研发执行报告
10. 查看实时 CLI 输出
11. 进入三个设置页


## 9. 当前不是 JSON API 优先

现阶段后端主要是模板渲染。

如果前端重做者只是改视觉层，直接改模板即可。

如果前端重做者想做成 SPA，建议下一步新增 JSON API，而不是去解析 HTML。
建议优先补的 JSON 接口是：

- `GET /api/projects`
- `GET /api/projects/{project_id}`
- `POST /api/projects`
- `POST /api/projects/{project_id}/step`
- `POST /api/projects/{project_id}/run`
- `GET /api/projects/{project_id}/runtime/stream`
- `GET /api/projects/{project_id}/tasks`
- `GET /api/projects/{project_id}/tasks/summary`
- `POST /api/projects/{project_id}/tasks/claim-next`
- `POST /api/projects/{project_id}/tasks/{assignment_id}/claim`
- `POST /api/projects/{project_id}/tasks/{assignment_id}/complete`
- `POST /api/projects/{project_id}/tasks/{assignment_id}/fail`
- `GET /api/settings/execution`
- `POST /api/settings/execution`
- `GET /api/settings/cli`
- `POST /api/settings/cli`
- `GET /api/settings/llm`
- `POST /api/settings/llm`
- `GET /api/diagnostics`

`GET /api/diagnostics` 默认只读取本地配置和已发现 CLI，不访问网络。
如需探测已启用的 OpenAI-compatible LLM `/models` 接口，可传 `?probe_llm=true`。
如需执行轻量 chat-completion 连通性验证，可传 `?preflight_llm=true`。

但这不是当前必须项。


## 10. 建议给前端模型的任务描述

可以直接把下面这段给另一个模型：

```text
基于现有 Conductor Board 功能，重做前端页面，但不要改后端业务逻辑。页面目标是“炫酷、科技感、控制台/战情室风格”，不是普通后台。必须保留：
1. 项目创建和项目目录选择
2. 项目列表和项目详情
3. 需求阶段会议桌
4. 当前需求文档、review 内容、历史版本
5. 当前执行上下文
6. CLI 实时输出面板
7. 研发执行报告
8. route/gate/recent events/blockers/project agents
9. step/run 操作
10. settings 入口

布局优先级：
- 左侧项目轨道
- 中间主舞台（会议桌 + 文档 + 报告）
- 右侧情报栏（运行态 + 实时输出 + 过程信息）

视觉方向：
- 深色
- 青蓝/电光紫高亮
- 玻璃面板
- 仪表盘 / 终端 / 科技中控
- 层级强，留白克制，模块边界清楚

不要只换颜色，要重做结构层级和信息组织。
```
