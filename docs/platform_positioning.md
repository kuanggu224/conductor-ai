# Conductor 平台定位

## 一、平台本质

Conductor 不是一个普通的 AI 聊天工具、Agent 调度器、Workflow 编排器或 AutoGPT 类系统。

Conductor 的定位是：

> 一个由 AI 扮演完整软件团队、能够自主推进项目从需求到交付的项目执行系统（Project Execution System）。

当前代码阶段性优先稳定核心控制层、后端/API 执行链路、Task Center、验证审计链路和平台自身管理控制台。这个实现重点不等于长期产品边界；前端开发、全栈交付、UI 设计、运维、数据分析和文档工程等能力仍属于长期可演进方向，是否恢复或新增应以明确设计和实现状态为准。

## 二、核心目标

平台的最终目标是：

```text
用户只描述需求
↓
系统自动理解目标
↓
自动拆解项目
↓
多 Agent 协作执行
↓
自动推进流程
↓
自动质量控制
↓
最终完成交付
```

也就是从“AI 帮人干活”进化到“AI 自己组织团队完成项目”。

## 三、平台想解决的问题

Conductor 想解决的不是“怎么调用多个 Agent”，而是：

> 怎么让 AI 像真正的软件团队一样长期协作完成项目。

## 四、核心设计目标

### 1. AI 能像团队一样工作

系统中存在 TL（Lead Controller）、Planner、Designer、Backend Engineer、Frontend Engineer、Tester、Reviewer 等角色。

这些角色不是人格模拟，而是职责系统。

### 2. 项目驱动（Project-driven）

Conductor 不是 Task-driven，而是 Project-driven。

这意味着系统需要有阶段、目标、依赖、流程推进、质量控制和最终交付。

### 3. AI 自主推进项目

Conductor 的核心不是生成内容，而是推进项目。

系统需要判断：

- 下一步做什么
- 当前是否完成
- 是否需要返工
- 是否需要升级
- 是否进入下一阶段

### 4. 控制力优先于智能感

平台更重视可控性，而不是让 Agent 看起来像真人。

因此：

- 不允许 Agent 自主推进流程
- 不允许 Agent 随意对话
- 所有协作通过 Shared State
- 所有推进必须经过 Controller

### 5. Human 可接管，但系统不依赖 Human

平台支持 AI-only、Human-controlled 和 Hybrid 三种运行模式。

核心目标是：即使没有人类参与，系统也能自主完成项目。

Human 的角色是 override、review、approval、strategy adjustment，而不是必须参与执行。

## 五、系统核心能力目标

### 1. 多 Agent 协作

多 Agent 协作不是多个 AI 同时说话，而是结构化协作。

包括：

- 任务拆解
- 上下文隔离
- 状态共享
- 阶段推进
- 结果汇总

### 2. 长周期执行

系统目标不是回答一次问题，而是持续推进一个项目。

这要求系统支持多阶段、多轮执行、多次返工、长时间运行和可恢复。

### 3. 可观测性

用户需要能够看到：

- 当前阶段
- 当前负责人
- 当前执行轮次
- 哪个 Agent 在工作
- 为什么被拒绝
- 为什么返工
- 当前 blocker
- 历史记录

平台本质上希望实现 AI 团队的透明化运作。

### 4. 可恢复 / 可回放

系统不是一次性对话，而是事件驱动项目系统。

因此需要：

- Event Log
- Shared State
- 阶段记录
- WorkItem 历史
- 决策历史

从而支持 replay、resume、audit 和 debug。

### 5. 模型与 Agent 解耦

Conductor 的核心思想之一是：

```text
Agent ≠ Model
```

Agent 是角色、能力和规则；模型只是执行后端。

因此未来可以支持 OpenAI、Claude、Gemini、Qwen、本地模型、CLI Agent 和外部 Harness。

## 六、长期形态

Conductor 的最终目标不是一个 AI IDE，而是一个 AI Native 的项目执行操作系统。

未来可覆盖：

- 软件开发
- UI 设计
- 测试
- 运维
- 数据分析
- 文档工程
- 自动化流程
- 企业项目协作

## 七、一句话定义

> Conductor 是一个由 AI 扮演完整团队、能够自主推进项目从需求到交付的可控项目执行系统。
