const DEMO_PROJECT_ID = "demo-api-delivery";

const workitems = [
  {
    id: "workitem-requirement",
    stage: "requirement",
    kind: "frozen_requirement_spec",
    kind_label: "需求规格",
    status: "done",
    status_label: "已完成",
    description: "冻结纯 API Todo 服务范围和验收契约。",
  },
  {
    id: "workitem-design",
    stage: "design",
    kind: "api_design",
    kind_label: "API 设计",
    status: "done",
    status_label: "已完成",
    description: "定义 REST 资源、SQLite 持久化、校验和统计端点。",
  },
  {
    id: "workitem-api",
    stage: "development",
    kind: "api_implementation",
    kind_label: "API 实现",
    status: "done",
    status_label: "已完成",
    description: "生成 FastAPI 服务、契约测试和清单证据。",
  },
  {
    id: "workitem-validation",
    stage: "testing",
    kind: "api_validation",
    kind_label: "API 验证",
    status: "running",
    status_label: "运行中",
    description: "运行 pytest 契约验证和需求覆盖检查。",
  },
  {
    id: "workitem-delivery",
    stage: "delivery",
    kind: "delivery_manifest",
    kind_label: "交付清单",
    status: "pending",
    status_label: "待处理",
    description: "写入最终报告、清单和回放审计包。",
  },
];

const taskAssignments = [
  {
    id: "assignment-api-validation",
    workitem_id: "workitem-validation",
    role: "tester",
    role_label: "测试",
    status: "claimable",
    status_label: "可领取",
    parallel_safe: true,
  },
  {
    id: "assignment-delivery-review",
    workitem_id: "workitem-delivery",
    role: "solution_designer",
    role_label: "方案设计",
    status: "queued",
    status_label: "排队中",
    parallel_safe: false,
  },
  {
    id: "assignment-api-rework",
    workitem_id: "workitem-api",
    role: "backend_engineer",
    role_label: "后端工程师",
    status: "done",
    status_label: "已完成",
    parallel_safe: true,
  },
];

const artifacts = [
  {
    id: "artifact-frozen-requirement",
    workitem_id: "workitem-requirement",
    kind: "frozen_requirement_spec",
    title: "冻结 API 需求",
    version: 1,
    source_backend: "llm_harness/local",
    content: "目标：纯 API Todo 服务，包含 SQLite 持久化、CRUD、筛选、删除和统计。非目标：浏览器 UI 和登录。",
  },
  {
    id: "artifact-api-implementation",
    workitem_id: "workitem-api",
    kind: "api_implementation",
    title: "FastAPI SQLite 实现",
    version: 1,
    source_backend: "api_sqlite_delivery",
    content: "变更文件：app.py、pytest.ini、tests/test_api_contract.py。验证结果：1 项通过，SQLite row_count=2。",
  },
  {
    id: "artifact-validation",
    workitem_id: "workitem-validation",
    kind: "api_validation",
    title: "API 契约验证",
    version: 1,
    source_backend: "cli/shell",
    content: "POST /api/items -> 201\nGET /api/items/stats -> 200\n需求覆盖：通过\n交付就绪度：就绪 / 92",
  },
];

const agents = [
  {
    agent_id: "agent-requirement",
    role: "requirement_designer",
    role_label: "需求",
    active: false,
    status_label: "空闲",
    mission: "冻结业务范围和下游验收契约。",
  },
  {
    agent_id: "agent-backend",
    role: "backend_engineer",
    role_label: "后端",
    active: true,
    status_label: "就绪",
    mission: "负责 API 实现、持久化和契约测试修复。",
  },
  {
    agent_id: "agent-tester",
    role: "tester",
    role_label: "测试",
    active: true,
    status_label: "可领取",
    mission: "验证可观测 API 行为和需求覆盖。",
  },
  {
    agent_id: "agent-reviewer",
    role: "solution_designer",
    role_label: "评审",
    active: false,
    status_label: "等待中",
    mission: "评审交付就绪度、风险和清单审计证据。",
  },
];

const activationNodes = [
  {
    agent_id: "dynamic-backend-contracts",
    role: "backend_engineer",
    role_label: "后端契约",
    active: true,
    status_label: "活跃",
    reason: "API 契约和数据持久化范围已拆分评审。",
  },
  {
    agent_id: "dynamic-tester-edge",
    role: "tester",
    role_label: "边界测试",
    active: true,
    status_label: "活跃",
    reason: "校验、删除和筛选行为需要边界场景覆盖。",
  },
];

const demoTodoItems = [
  {
    id: 1,
    title: "验证 API 契约",
    content: "确认 POST /api/items 和 GET /api/items/stats 证据可见。",
    completed: true,
  },
  {
    id: 2,
    title: "评审交付清单",
    content: "检查就绪度分数、产物列表和回放验证备注。",
    completed: false,
  },
  {
    id: 3,
    title: "准备实时模式兜底",
    content: "如果实时后端未运行，保留演示模式可用。",
    completed: false,
  },
];

const demoSettings = {
  execution: {
    config: {
      run_profile: "api_sqlite",
      max_parallel_tasks: 2,
      require_artifacts: true,
      static_smoke: true,
      backend_checks: "optional",
    },
    source: "demo-fixture",
    updated_at: "2026-06-22T10:30:00+08:00",
  },
  cli: {
    config: {
      agent_cli_required: false,
      shell: "powershell",
      fallback: "离线演示夹具",
      workspace: "C:/99_self/conductor/conductor-ai",
    },
    source: "demo-fixture",
    updated_at: "2026-06-22T10:30:00+08:00",
  },
  llm: {
    config: {
      provider: "offline",
      model: "demo-runner",
      enabled: false,
      note: "演示模式渲染确定性夹具，不调用模型服务商。",
    },
    source: "demo-fixture",
    updated_at: "2026-06-22T10:30:00+08:00",
  },
};

const demoCapabilities = [
  {
    area: "依赖图",
    demo: "本地夹具提供确定性的就绪度推进。",
    live: "由 /api/projects/{id} 快照和运行时流刷新支撑。",
    status: "已对齐",
  },
  {
    area: "任务中心",
    demo: "本地模拟领取、清理和任务上下文结果。",
    live: "由任务领取、释放、心跳、清理和上下文 API 支撑。",
    status: "已对齐",
  },
  {
    area: "智能体",
    demo: "花名册、动态激活和可领取工作来自夹具数据。",
    live: "由项目智能体分配和智能体任务 API 支撑。",
    status: "已对齐",
  },
  {
    area: "评审产物",
    demo: "FastAPI、验证和清单证据来自夹具产物。",
    live: "由已存储项目产物和人工控制 API 支撑。",
    status: "已对齐",
  },
  {
    area: "Todo API",
    demo: "CRUD 状态在浏览器会话中本地变更。",
    live: "由平台 Todo API 和 Cookie 作用域会话存储支撑。",
    status: "已对齐",
  },
  {
    area: "外部执行",
    demo: "不执行 Agent CLI 和 LLM 调用。",
    live: "真实运行前需要配置 Agent CLI 和/或 LLM 后端。",
    status: "需要配置",
  },
];

export function demoProjects() {
  return [
    {
      project_id: DEMO_PROJECT_ID,
      status: "in_progress",
      status_label: "演示",
      goal: demoGoal(),
    },
  ];
}

export function createDemoProject(step = 0) {
  return {
    project_id: DEMO_PROJECT_ID,
    snapshot: demoSnapshot(step),
  };
}

export function advanceDemoProject(project, action = "step") {
  const current = Number(project?.snapshot?.demo_step || 0);
  const next = action === "run" ? 2 : Math.min(current + 1, 2);
  return createDemoProject(next);
}

export function demoArtifact(artifactId) {
  const artifact = artifacts.find((item) => item.id === artifactId);
  return artifact ? { artifact, content: artifact.content } : { artifact_id: artifactId, content: "未找到演示产物。" };
}

export function demoTaskContext(assignmentId) {
  const assignment = taskAssignments.find((item) => item.id === assignmentId);
  return {
    assignment_id: assignmentId,
    workitem_id: assignment?.workitem_id || "",
    agent_prompt: "执行已分配的 API 验证任务，保持冻结范围，并返回具体证据。",
    required_inputs: ["frozen_requirement_spec", "api_implementation"],
    expected_outputs: ["验证证据", "覆盖追踪", "交付就绪信号"],
  };
}

export function demoOperationResult(label) {
  return {
    ok: true,
    mode: "demo",
    message: `${label} 已记录到本地演示状态。`,
    timestamp: "2026-06-22T10:30:00+08:00",
  };
}

export function demoTodos() {
  return { todos: demoTodoItems.map((item) => ({ ...item })) };
}

export function demoTodoStats() {
  const completed = demoTodoItems.filter((item) => item.completed).length;
  return {
    total: demoTodoItems.length,
    completed,
    active: demoTodoItems.length - completed,
  };
}

export function demoTodoDetail(todoId) {
  const id = Number(todoId);
  const todo = demoTodoItems.find((item) => item.id === id) || demoTodoItems[0];
  return { todo: { ...todo }, mode: "demo" };
}

export function demoSettingsPayload() {
  return {
    execution: clone(demoSettings.execution),
    cli: clone(demoSettings.cli),
    llm: clone(demoSettings.llm),
  };
}

export function demoCapabilityMatrix() {
  return clone(demoCapabilities);
}

export function demoLlmPreflight() {
  return {
    ok: true,
    mode: "demo",
    provider: "offline",
    checks: [
      { name: "configuration", status: "pass", detail: "离线夹具可用。" },
      { name: "model_call", status: "skipped", detail: "演示模式不调用外部 LLM。" },
      { name: "fallback", status: "pass", detail: "确定性交付数据已本地加载。" },
    ],
  };
}

export function demoDiagnostics(probe = false) {
  return {
    ok: true,
    mode: "demo",
    probe_requested: Boolean(probe),
    backend_required: false,
    checks: {
      frontend_fixture: "pass",
      static_assets: "pass",
      browser_smoke: "pass",
      live_api: "演示模式不需要",
      cli_probe: probe ? "离线演示中跳过" : "未请求",
      llm_preflight: probe ? "离线夹具通过" : "未请求",
    },
    recommended_url: "http://127.0.0.1:4176/?demo=1",
  };
}

function demoSnapshot(step) {
  const validationStatus = step >= 1 ? "done" : "running";
  const deliveryStatus = step >= 2 ? "done" : "pending";
  const readiness = step >= 2 ? 96 : step >= 1 ? 92 : 78;
  const risk = step >= 1 ? "low" : "medium";
  const updatedWorkitems = workitems.map((item) => {
    if (item.id === "workitem-validation") return { ...item, status: validationStatus, status_label: label(validationStatus) };
    if (item.id === "workitem-delivery") return { ...item, status: deliveryStatus, status_label: label(deliveryStatus) };
    return item;
  });
  return {
    demo_step: step,
    project_id: DEMO_PROJECT_ID,
    project_goal: demoGoal(),
    project_status: step >= 2 ? "ready" : "in_progress",
    project_status_label: step >= 2 ? "就绪" : "进行中",
    current_stage: step >= 2 ? "delivery" : step >= 1 ? "testing" : "development",
    run_audit: {
      risk_level: risk,
      risk_level_label: label(risk),
      delivery_readiness_status: step >= 2 ? "ready" : "at_risk",
      delivery_readiness_status_label: step >= 2 ? "就绪" : "有风险",
      delivery_readiness_score: readiness,
    },
    task_center_summary: {
      queued: step >= 2 ? 0 : 1,
      claimable: step >= 1 ? 0 : 1,
      claimed: step >= 1 ? 1 : 0,
      done: step >= 2 ? 3 : 2,
    },
    blockers: step >= 1 ? [] : ["验证范围仍在运行。"],
    workitems: updatedWorkitems,
    task_assignments: taskAssignments.map((item) => {
      if (item.id === "assignment-api-validation" && step >= 1) return { ...item, status: "done", status_label: "已完成" };
      if (item.id === "assignment-delivery-review" && step >= 2) return { ...item, status: "done", status_label: "已完成" };
      return item;
    }),
    project_agents: agents,
    activation_nodes: activationNodes,
    artifacts: step >= 1 ? artifacts : artifacts.slice(0, 2),
    human_control: {
      active: false,
      hold_reason: "",
      next_action: "operator_review",
    },
    operation_console: {
      guidance: step >= 2 ? "交付清单已准备好评审。" : "交付前继续验证或检查任务上下文。",
      actions: [
        { id: "claim-next", label: "领取下一项", enabled: step < 2, category: "task_center", api_method: "POST", api_path: `/api/projects/${DEMO_PROJECT_ID}/tasks/claim-next` },
        { id: "sweep", label: "清理任务", enabled: true, category: "task_center", api_method: "POST", api_path: `/api/projects/${DEMO_PROJECT_ID}/tasks/sweep` },
        { id: "approve", label: "批准", enabled: step >= 2, category: "human_control", api_method: "POST", api_path: `/api/projects/${DEMO_PROJECT_ID}/human-control/approve` },
      ],
    },
    recent_events_tail: [
      "项目 demo-api-delivery 已按纯 API 运行配置创建。",
      "WorkItem workitem-api 使用 ApiSqliteDelivery 生成可验证的 SQLite API 服务。",
      "POST /api/items -> status_code=201 response payload={item}",
      step >= 1 ? "需求覆盖：通过；交付就绪度：有风险 / 92" : "验证任务运行中。",
      step >= 2 ? "清单验证通过；交付报告已写入。" : "等待最终交付评审。",
    ],
    route_lines: [
      "requirement -> design -> development -> testing -> delivery",
      "api_implementation -> api_validation -> delivery_manifest",
    ],
    executions: [
      { workitem_id: "workitem-api", agent_role: "backend_engineer", agent_label: "后端", status: "success", status_label: "成功", source_backend: "api_sqlite_delivery" },
      { workitem_id: "workitem-validation", agent_role: "tester", agent_label: "测试", status: validationStatus === "done" ? "success" : "running", status_label: label(validationStatus), source_backend: "cli/shell" },
      { workitem_id: "workitem-delivery", agent_role: "solution_designer", agent_label: "评审", status: deliveryStatus, status_label: label(deliveryStatus), source_backend: "manifest" },
    ],
  };
}

function demoGoal() {
  return "构建纯 API Todo 服务，包含 FastAPI、SQLite 持久化、CRUD、删除、筛选、统计、契约测试和清单审计证据。";
}

function label(value) {
  const labels = {
    at_risk: "有风险",
    claimable: "可领取",
    done: "已完成",
    in_progress: "进行中",
    low: "低",
    medium: "中",
    pending: "待处理",
    queued: "排队中",
    ready: "就绪",
    running: "运行中",
    success: "成功",
  };
  return labels[value] || String(value || "").replace(/_/g, " ").replace(/\b\w/g, (char) => char.toUpperCase());
}

function clone(value) {
  return JSON.parse(JSON.stringify(value));
}
