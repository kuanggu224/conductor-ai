# 需求环节产品级优化计划

## 目标

需求环节的目标不是让多个 Agent 进行自由聊天，而是让多个职责视角共同参与同一份需求草案的审查、修订和冻结。

最终希望达到：

```text
用户原始需求
↓
需求草案
↓
多职责视角审查
↓
统一修订
↓
冻结需求规格
↓
后续设计、开发、测试以冻结规格为基线
```

## 产品级标准

需求环节必须满足以下标准，不能只停留在记录讨论内容：

- 产出明确的冻结需求规格（Frozen Requirement Spec）。
- 需求评审不通过时，不允许静默进入开发。
- 评审结果必须影响 Gate。
- 后续阶段必须能追溯需求基线。
- 每次需求产物都能被评分。
- 平台产物必须能和模型直出结果按同一标准对比。

## 当前已落地

- 多 Agent 设计/需求协作评审已有主流程接入。
- 默认工作流已升级为 `requirement -> design -> development -> testing`。
- 旧 `system.config.json` 加载时会自动补入 `requirement` 阶段、`requirement_spec` 路由和需求协作策略。
- `requirement` 阶段会生成 `requirement_spec` WorkItem，由 `requirement_designer` 负责。
- 协作 accepted 后会额外生成 `frozen_requirement_spec` artifact。
- 协作未 accepted 时，当前 WorkItem 会被标记为 `validation_failed`，并进入失败/阻塞路径。
- `frozen_requirement_spec` 已加入 Artifact Contract。
- 新增需求评分模块：`conductor.requirement_benchmark`。
- 新增 CLI：`python -m app.requirement_benchmark`。
- 支持从 Conductor manifest 读取平台需求产物。
- 支持读取模型直出文本文件作为 direct baseline。
- 支持输出 JSON 和 Markdown 对比报告。
- 需求评分支持中英文 aspect 同义词匹配、中文关键词变体匹配、`metrics.keyword_matches` 命中证据和范围扩张检测，避免中文需求产物被英文标签或同义表达误判，也避免下游文档静默加入原始需求未要求的功能。
- Run Manifest schema 已升级到 `1.28`，包含 `requirement_evaluations`、`summary.requirement_quality_score`、preflight gate 索引、preflight 修复建议、`retry_history`、`scope_contract_results`、`prompt_hash`、`summary.llm_context_windows`、`summary.llm_token_usage`、`summary.llm_cost_estimate`、`resume_cursor` 和运行审计摘要。
- `python -m app.requirement_benchmark run-suite` 的 `requirement-generated-suite.json` 会记录 `run_config.requirement_review_mode`、`run_config.dynamic_requirement_review_enabled`、`platform_runs[].requirement_review_mode` 和 `platform_runs[].collaboration_max_rounds`，便于对比 static review 与 dynamic review。
- `requirement_spec` 的协作 accepted 后还会执行需求质量评分；评分未通过时不会冻结需求规格。
- 需求协作/质量门禁失败时，会自动创建新的 `requirement_spec` 返工 WorkItem，并把失败原因和上轮产物作为返工上下文。
- `python -m app.requirement_benchmark compare` 支持 `--direct-llm local|cloud`，可自动生成不接入平台的模型直出 baseline。
- 需求阶段已加入动态评审团队规划：系统会基于需求中的 UI、数据、API、流程、角色、校验、导出、安全等复杂度信号，动态扩展评审席位。
- 动态评审不是自由聊天，而是结构化 review seats；复杂项目可以出现多个同职责席位，例如 `designer.interaction`、`designer.information_architecture`、`solution_designer.process`、`tester.edge_cases`。
- Collaboration 运行时会记录动态团队规划事件，并把虚拟评审席位写入 `reviewer_agent_ids` 和评审贡献记录。
- Collaboration 会持久化 `team_plan`，Run Manifest 的 `collaboration_runs[].team_plan` 可直接审计复杂度、触发原因和评审席位。
- Board Snapshot 的 `design_collaboration.team_plan` 会暴露同一份结构化计划，前端可直接展示需求评审团队组成原因。
- Board 模板已渲染 Team Plan 面板，展示复杂度、触发原因、评审席位和每个席位的 focus。
- `python -m app.requirement_benchmark suite` 支持按目录批量比较多个固定 case 的平台需求产物和 direct baseline。
- `python -m app.requirement_benchmark run-suite` 支持一键生成平台需求产物、读取或生成 direct baseline，并输出同一套对比报告。
- `python -m app.requirement_benchmark preflight` 支持在真实测评前验证 local/cloud LLM backend 是否可连接。
- `run-suite` 默认会对 `--platform-llm` / `--direct-llm` 涉及的 backend 做 preflight；如需跳过可显式传 `--skip-preflight`。
- 需求评分器会识别 `mock` / `mock_fallback` / 占位文档并降权，避免把平台模板外壳误判为真实需求质量。
- 需求评分维度已扩展到非目标、待确认/假设、边界/异常场景、下游交付约束，不再只依赖关键词和基础章节。
- `run-suite` 生成的 JSON 会记录 platform/direct 两侧运行元数据；Markdown 报告会展开每个质量检查项的逐项对比。
- 需求门禁返工已设置上限，连续返工仍失败时会阻塞项目并暴露 blocker，避免无限派生需求 WorkItem。
- 需求生成/修订 prompt 已同步到质量门禁维度：`requirement_spec` 必须覆盖非目标、边界/异常场景、风险与假设、待确认问题、下游交付约束。
- 需求协作加入 Controller 终局裁决：最后一轮 reviewer 提出 `request_changes` 后，如果 lead 最终修订已经通过需求评分，并覆盖修改意见中的核心主题，系统会直接 accepted 并冻结需求，而不是机械地因为达到最大轮次创建返工。
- 同一轮内已按 peer review 修订过的意见不会被重复修订，减少本地小模型的无效耗时。

## 真实测评结果

本轮用 LM Studio 本地 `qwen2.5-coder-14b-instruct` 跑了完整固定 case 套件：

```powershell
python -m app.requirement_benchmark run-suite `
  --output-dir C:\tmp\conductor_requirement_real_qwen25_suite_plain_arbitrated `
  --platform-llm local `
  --direct-llm local `
  --direct-prompt-mode plain `
  --llm-base-url http://127.0.0.1:1234/v1 `
  --llm-model qwen2.5-coder-14b-instruct `
  --llm-timeout 180 `
  --max-steps 4 `
  --collaboration-max-rounds 1 `
  --static-requirement-review
```

结果：

| Case | Platform | Direct Plain | Delta | Result |
| --- | ---: | ---: | ---: | --- |
| `reading_list` | 100 | 42 | 58 | passed |
| `expense_approval` | 87 | 39 | 48 | passed |
| `csv_cleaner` | 88 | 44 | 44 | passed |

汇总：

- 3/3 passed。
- Platform wins: 3。
- Average platform score: 91.67。
- Average direct score: 41.67。
- Average delta: 50.0。
- 报告：`C:\tmp\conductor_requirement_real_qwen25_suite_plain_arbitrated\requirement-comparison-results.md`
- JSON：`C:\tmp\conductor_requirement_real_qwen25_suite_plain_arbitrated\requirement-comparison-results.json`

结论：

- 在 plain direct baseline 下，需求阶段平台链路已经能显著放大小模型的需求规格输出质量。
- 本次通过不是 mock/fallback 通过，platform/direct 两侧都使用同一个本地真实模型。
- `expense_approval` 和 `csv_cleaner` 曾出现 keyword coverage 偏低提示；当前评分器已补充中文关键词变体、命中计数和缺失关键词明细，后续仍需用更多真实 case 校准。

## 测评命令

单独评分：

```powershell
python -m app.requirement_benchmark score `
  --case reading_list `
  --manifest C:\path\to\project\.conductor\manifests\project-id.manifest.json
```

平台产物对比模型直出：

```powershell
python -m app.requirement_benchmark compare `
  --case reading_list `
  --platform-manifest C:\path\to\project\.conductor\manifests\project-id.manifest.json `
  --direct-file C:\path\to\direct-output.md `
  --output-dir C:\path\to\requirement-benchmark
```

也可以用 `--platform-file` 直接传入平台需求文档文件。

批量对比固定 case：

```powershell
python -m app.requirement_benchmark suite `
  --platform-dir C:\path\to\platform-outputs `
  --direct-dir C:\path\to\direct-outputs `
  --output-dir C:\path\to\requirement-benchmark
```

其中平台目录可放置：

- `<case>.platform.md`
- `<case>.md`
- `<case>.manifest.json`
- `<case>\.conductor\manifests\*.manifest.json`

直出目录可放置：

- `<case>.direct-requirement.md`
- `<case>.direct.md`
- `<case>.md`

一键生成平台需求产物并对比：

```powershell
python -m app.requirement_benchmark preflight `
  --backend local `
  --llm-base-url http://127.0.0.1:1234/v1 `
  --llm-model qwen3.6-35b-a3b `
  --output-dir C:\path\to\requirement-preflight
```

```powershell
python -m app.requirement_benchmark run-suite `
  --cases reading_list expense_approval `
  --output-dir C:\path\to\requirement-e2e `
  --platform-llm local `
  --direct-llm local `
  --llm-base-url http://127.0.0.1:1234/v1 `
  --llm-model qwen3.6-35b-a3b
```

如果不传 `--platform-llm`，平台侧会使用 mock/fallback 产物；这类产物会被评分器降权，不能作为真实产品级通过证据。

## 下一步

1. 扩大真实测评：加入动态评审队伍、更多复杂 case 和云模型/本地模型对照。
2. 继续校准评分器：增强领域实体覆盖、范围扩张检测和中文分词边界处理。
3. 根据真实测评结果继续调整需求 prompt、动态评审席位规则和质量门禁阈值。
4. 将需求冻结产物继续向设计/开发/测试阶段传递，验证完整项目闭环。
