import { ApiClient, getApiBase } from "./api.js";
import { advanceDemoProject, createDemoProject, demoArtifact, demoCapabilityMatrix, demoDiagnostics, demoLlmPreflight, demoOperationResult, demoProjects, demoSettingsPayload, demoTaskContext, demoTodoDetail, demoTodos } from "./demo.js";
import { asArray, el, empty, pretty, short, tone, valueOf } from "./utils.js";

const api = new ApiClient();
const app = document.getElementById("app");

const nav = [
  ["graph", "依赖图"],
  ["tasks", "任务"],
  ["agents", "智能体"],
  ["review", "评审"],
  ["logs", "日志"],
  ["settings", "设置"],
  ["todos", "Todo"],
];

const stageLabels = {
  requirement: "需求",
  design: "设计",
  development: "开发",
  testing: "测试",
  delivery: "交付",
};

const nodeTypeLabels = {
  artifact: "产物",
  "human gate": "人工门禁",
  lease: "任务租约",
  stage: "阶段",
  task: "工作项",
};

const commandLabels = {
  Approve: "批准",
  "Batch Claim": "批量领取",
  "Claim Next": "领取下一项",
  Sweep: "清理任务",
};

const humanActions = [
  ["pause", "暂停"],
  ["resume", "恢复"],
  ["request-approval", "请求审批"],
  ["approve", "批准"],
  ["reject", "拒绝"],
  ["override", "覆盖"],
];

const state = {
  route: routeFromHash(),
  projects: [],
  selectedProjectId: localStorage.getItem("conductor.selectedProjectId") || "",
  project: null,
  node: null,
  detail: null,
  context: null,
  settings: {},
  diagnostics: null,
  apiStatus: null,
  todos: null,
  todoStats: null,
  todoDetail: null,
  loading: false,
  error: "",
  toast: "",
  stream: null,
  demoMode: demoModeFromUrl() || localStorage.getItem("conductor.demoMode") === "1",
};

init();
window.addEventListener("hashchange", () => {
  setRoute(routeFromHash());
  renderShell();
});

async function init() {
  renderShell();
  await refreshAll();
}

function routeFromHash() {
  const route = location.hash.replace(/^#/, "") || "graph";
  return nav.some(([id]) => id === route) ? route : "graph";
}

function demoModeFromUrl() {
  return new URLSearchParams(window.location.search).get("demo") === "1";
}

function navigate(route) {
  setRoute(route);
  if (location.hash !== `#${route}`) location.hash = route;
  else renderShell();
}

function setRoute(route) {
  if (state.route === route) return;
  clearRouteTransientDetail();
  state.route = route;
}

function clearRouteTransientDetail() {
  state.detail = null;
  state.context = null;
}

async function refreshAll() {
  await busy(async () => {
    if (state.demoMode) {
      loadDemoProject();
      return;
    }
    const payload = await api.listProjects();
    state.projects = asArray(payload.projects);
    if (state.selectedProjectId && !state.projects.some((project) => project.project_id === state.selectedProjectId)) {
      state.selectedProjectId = "";
    }
    if (!state.selectedProjectId && state.projects[0]) state.selectedProjectId = state.projects[0].project_id;
    if (state.selectedProjectId) await loadProject(state.selectedProjectId, true);
  });
}

async function loadProject(projectId, silent = false) {
  if (state.demoMode) {
    loadDemoProject();
    return;
  }
  state.selectedProjectId = projectId || "";
  localStorage.setItem("conductor.selectedProjectId", state.selectedProjectId);
  if (!state.selectedProjectId) {
    state.project = null;
    renderShell();
    return;
  }
  if (!silent) state.loading = true;
  try {
    state.project = await api.project(state.selectedProjectId);
    state.error = "";
    wireRuntimeStream();
  } catch (error) {
    state.error = error.message;
  } finally {
    state.loading = false;
    renderShell();
  }
}

function wireRuntimeStream() {
  if (state.stream) state.stream.close();
  state.stream = null;
  if (state.demoMode || !state.selectedProjectId || typeof EventSource === "undefined") return;
  try {
    state.stream = new EventSource(`${api.baseUrl}/projects/${encodeURIComponent(state.selectedProjectId)}/runtime/stream`);
    state.stream.onmessage = async () => {
      if (state.route === "graph" || state.route === "logs") {
        try {
          state.project = await api.project(state.selectedProjectId);
          render();
        } catch {
          state.stream?.close();
        }
      }
    };
    state.stream.onerror = () => state.stream?.close();
  } catch {
    state.stream = null;
  }
}

function snapshot() {
  return state.project?.snapshot || {};
}

function renderShell() {
  empty(app);
  app.append(
    el("aside", { class: "sidebar" }, [
      el("div", { class: "brand" }, [el("strong", { text: "CONDUCTOR" }), el("small", { text: "智能体控制台" })]),
      el("nav", {}, nav.map(([id, label]) => el("button", { class: `nav-btn ${state.route === id ? "active" : ""}`, onclick: () => navigate(id) }, [el("span", { text: label })]))),
    ]),
    el("main", { class: "workspace" }, [
      renderTopbar(),
      el("section", { id: "view", class: "view" }),
    ])
  );
  render();
}

function renderTopbar() {
  const snap = snapshot();
  return el("header", { class: "topbar" }, [
    el("div", { class: "topbar-left" }, [
      el("select", { class: "project-select", onchange: (event) => loadProject(event.target.value) }, [
        el("option", { value: "", text: "选择项目" }),
        ...displayProjects().map((project) => el("option", {
          value: project.project_id,
          selected: project.project_id === state.selectedProjectId,
          text: `${project.status_label || project.status || "-"} / ${short(project.goal || project.project_id, 52)}`,
        })),
      ]),
      badge(snap.project_status_label || snap.project_status || "未选择项目", tone(snap.project_status)),
      state.demoMode ? badge("演示", "warn") : null,
      state.demoMode ? badge(demoProgressLabel(snap), "info") : null,
      badge(api.baseUrl, "info"),
    ]),
    el("div", { class: "topbar-actions" }, [
      cmd(state.demoMode ? "实时" : "演示", toggleDemoMode),
      cmd("新建", openCreateProject),
      cmd("推进", () => projectCommand("step"), !state.selectedProjectId),
      cmd("运行", () => projectCommand("run"), !state.selectedProjectId),
      cmd("刷新", refreshAll),
    ]),
  ]);
}

function displayProjects() {
  return state.demoMode ? demoProjects() : state.projects;
}

function loadDemoProject(step = Number(state.project?.snapshot?.demo_step || 0), resetTodos = false) {
  state.projects = demoProjects();
  state.selectedProjectId = state.projects[0].project_id;
  state.project = createDemoProject(step);
  ensureDemoTodos(resetTodos);
  state.error = "";
  localStorage.setItem("conductor.selectedProjectId", state.selectedProjectId);
}

function toggleDemoMode() {
  state.demoMode = !state.demoMode;
  localStorage.setItem("conductor.demoMode", state.demoMode ? "1" : "0");
  state.toast = state.demoMode ? "演示模式已加载。" : "";
  state.apiStatus = null;
  if (state.demoMode) {
    loadDemoProject(undefined, true);
    renderShell();
  } else {
    state.projects = [];
    state.project = null;
    state.error = "";
    renderShell();
    refreshAll();
  }
}

function demoProgressLabel(snap) {
  const step = Number(snap.demo_step || 0);
  if (step >= 2) return "路径 4/4";
  if (step >= 1) return "路径 3/4";
  return "路径 1/4";
}

function render() {
  const view = document.getElementById("view");
  if (!view) return;
  empty(view);
  if (state.error) view.append(renderErrorBanner(state.error));
  if (state.toast) view.append(el("div", { class: "banner good", text: state.toast }));
  if (state.loading) view.append(el("div", { class: "banner info", text: "加载中..." }));
  const renderer = {
    graph: renderGraph,
    tasks: renderTasks,
    agents: renderAgents,
    review: renderReview,
    logs: renderLogs,
    settings: renderSettings,
    todos: renderTodos,
  }[state.route] || renderGraph;
  view.append(renderer());
}

function renderGraph() {
  if (!state.project) return emptyState("未选择项目", "创建、选择或加载演示项目后查看依赖图。");
  const snap = snapshot();
  const graph = buildGraph(snap);
  const selected = state.node ? graph.nodes.find((node) => node.id === state.node) : graph.nodes[0];
  return el("div", { class: "graph-layout" }, [
    el("aside", { class: "risk-panel" }, [
      sectionTitle("风险控制", "依赖健康度与人工关注点"),
      metric("风险", snap.run_audit?.risk_level_label || snap.run_audit?.risk_level || "-"),
      metric("就绪度", `${snap.run_audit?.delivery_readiness_status_label || "-"} / ${snap.run_audit?.delivery_readiness_score || 0}`),
      metric("可领取", snap.task_center_summary?.claimable || 0),
      metric("阻塞", asArray(snap.blockers).length),
      el("div", { class: "command-stack" }, [
        cmd("实时状态", () => loadLiveState()),
        cmd("领取下一项", () => claimNextTask()),
        cmd("清理任务", () => sweepTasks()),
        cmd("释放过期", () => releaseStale()),
        cmd("人工门禁", () => loadHumanControl()),
      ]),
      state.demoMode ? renderDemoRunbook(snap) : null,
    ]),
    el("section", { class: "graph-canvas" }, [
      el("div", { class: "graph-toolbar" }, [
        sectionTitle("依赖图", short(snap.project_goal || "当前项目", 92)),
        el("div", { class: "legend" }, [["正常", "good"], ["关注", "warn"], ["异常", "bad"], ["信息", "info"]].map(([label, name]) => badge(label, name))),
      ]),
      renderSvgGraph(graph),
    ]),
    el("aside", { class: "inspector" }, [
      sectionTitle(selected?.label || "检查器", displayNodeType(selected?.type) || "选择一个图节点"),
      selected ? renderNodeInspector(selected) : el("p", { class: "muted", text: "未选择节点。" }),
      renderOperationConsole(snap),
      renderGraphResult(),
    ]),
  ]);
}

function renderErrorBanner(message) {
  return el("div", { class: "banner bad action-banner" }, [
    el("div", { class: "banner-copy" }, [
      el("strong", { text: "实时 API 不可用" }),
      el("span", { text: message }),
      el("small", { text: "可以加载离线演示作为展示兜底，或打开设置检查后端 URL。" }),
    ]),
    state.demoMode ? null : cmd("加载演示", enableDemoMode),
    state.demoMode ? null : cmd("设置", () => navigate("settings")),
  ]);
}

function enableDemoMode() {
  state.demoMode = true;
  localStorage.setItem("conductor.demoMode", "1");
  state.toast = "演示模式已加载。";
  loadDemoProject(0, true);
  renderShell();
}

function renderDemoRunbook(snap) {
  const step = Number(snap.demo_step || 0);
  const items = [
    {
      title: "1. 查看依赖图",
      body: "展示阶段、工作项、任务分配、智能体、产物和人工门禁。",
      done: true,
    },
    {
      title: "2. 推进验证",
      body: "点击推进，预期风险为低，就绪度为有风险 / 92，阻塞为 0。",
      done: step >= 1,
    },
    {
      title: "3. 运行交付",
      body: "点击运行，预期项目就绪，就绪度为就绪 / 96，批准按钮可用。",
      done: step >= 2,
    },
    {
      title: "4. 评审证据",
      body: "打开评审，检查 FastAPI SQLite 实现和 API 契约验证。",
      done: step >= 2,
    },
  ];
  return el("div", { class: "demo-runbook" }, [
    el("h3", { text: "演示步骤" }),
    el("p", { text: step >= 2 ? "交付故事已准备好进入评审页。" : "展示时按这些步骤推进。" }),
    ...items.map((item) => el("div", { class: `demo-step ${item.done ? "done" : "todo"}` }, [
      el("strong", { text: item.title }),
      el("span", { text: item.body }),
    ])),
  ]);
}

function buildGraph(snap) {
  const stages = ["requirement", "design", "development", "testing", "delivery"];
  const nodes = [];
  const links = [];
  const stageY = { requirement: 130, design: 210, development: 300, testing: 390, delivery: 470 };
  const stageX = { requirement: 130, design: 310, development: 520, testing: 720, delivery: 890 };
  stages.forEach((stage, index) => {
    nodes.push({ id: `stage:${stage}`, type: "stage", label: stageLabels[stage] || stage.toUpperCase(), x: stageX[stage], y: 70, status: index <= stages.indexOf(snap.current_stage || "requirement") ? "running" : "pending" });
    if (index) links.push({ from: `stage:${stages[index - 1]}`, to: `stage:${stage}`, status: "info" });
  });
  for (const item of asArray(snap.workitems)) {
    const stage = item.stage || "delivery";
    nodes.push({ id: `work:${item.id}`, type: "task", label: item.kind_label || item.kind || item.id, sub: item.id, x: (stageX[stage] || 520) + jitter(item.id, 42), y: (stageY[stage] || 300) + jitter(item.description, 26), status: item.status, raw: item });
    links.push({ from: `stage:${stage}`, to: `work:${item.id}`, status: item.status });
  }
  for (const assignment of asArray(snap.task_assignments)) {
    nodes.push({ id: `assign:${assignment.id}`, type: "lease", label: assignment.role_label || assignment.role || "智能体任务", sub: assignment.status_label || assignment.status, x: 500 + jitter(assignment.id, 260), y: 170 + jitter(assignment.role, 260), status: assignment.status, raw: assignment });
    links.push({ from: `work:${assignment.workitem_id}`, to: `assign:${assignment.id}`, status: assignment.status });
  }
  for (const agent of asArray(snap.project_agents).slice(0, 8)) {
    nodes.push({ id: `agent:${agent.agent_id}`, type: "agent", label: agent.role_label || agent.role, sub: agent.agent_id, x: 120 + jitter(agent.agent_id, 760), y: 545, status: "ready", raw: agent });
  }
  for (const artifact of asArray(snap.artifacts).slice(-8)) {
    nodes.push({ id: `artifact:${artifact.id}`, type: "artifact", label: artifact.title || artifact.kind, sub: artifact.kind, x: 760 + jitter(artifact.id, 130), y: 150 + jitter(artifact.title, 300), status: "ready", raw: artifact });
    if (artifact.workitem_id) links.push({ from: `work:${artifact.workitem_id}`, to: `artifact:${artifact.id}`, status: "good" });
  }
  nodes.push({ id: "gate:human", type: "human gate", label: "人工门禁", sub: snap.human_control?.active ? snap.human_control.hold_reason : "操作员就绪", x: 910, y: 350, status: snap.human_control?.active ? "blocked" : "ready", raw: snap.human_control });
  if (nodes.some((node) => node.id.startsWith("assign:"))) links.push({ from: nodes.find((node) => node.id.startsWith("assign:"))?.id, to: "gate:human", status: snap.human_control?.active ? "bad" : "info" });
  return { nodes, links };
}

function renderSvgGraph(graph) {
  const ns = "http://www.w3.org/2000/svg";
  const svg = document.createElementNS(ns, "svg");
  svg.setAttribute("viewBox", "0 0 1040 640");
  svg.classList.add("dependency-svg");
  for (const link of graph.links) {
    const from = graph.nodes.find((node) => node.id === link.from);
    const to = graph.nodes.find((node) => node.id === link.to);
    if (!from || !to) continue;
    const path = document.createElementNS(ns, "path");
    const mid = (from.x + to.x) / 2;
    path.setAttribute("d", `M ${from.x} ${from.y} C ${mid} ${from.y}, ${mid} ${to.y}, ${to.x} ${to.y}`);
    path.setAttribute("class", `edge ${tone(link.status)}`);
    svg.append(path);
  }
  for (const node of graph.nodes) {
    const group = document.createElementNS(ns, "g");
    group.setAttribute("class", `node ${node.type.replace(/\s+/g, "-")} ${tone(node.status)} ${state.node === node.id ? "selected" : ""}`);
    group.setAttribute("transform", `translate(${node.x} ${node.y})`);
    group.addEventListener("click", () => { state.node = node.id; render(); });
    const rect = document.createElementNS(ns, "rect");
    rect.setAttribute("x", "-62"); rect.setAttribute("y", "-24"); rect.setAttribute("width", "124"); rect.setAttribute("height", "48"); rect.setAttribute("rx", "10");
    const label = document.createElementNS(ns, "text");
    label.setAttribute("text-anchor", "middle"); label.setAttribute("y", "-3"); label.textContent = short(node.label, 20);
    const sub = document.createElementNS(ns, "text");
    sub.setAttribute("text-anchor", "middle"); sub.setAttribute("y", "15"); sub.setAttribute("class", "sub"); sub.textContent = short(node.sub || node.type, 24);
    group.append(rect, label, sub);
    svg.append(group);
  }
  return svg;
}

function renderNodeInspector(node) {
  return el("div", { class: "inspector-block" }, [
    metric("类型", displayNodeType(node.type)),
    metric("状态", displayStatus(node.status || "-")),
    node.raw ? el("pre", { class: "code", text: pretty(node.raw) }) : null,
  ]);
}

function renderOperationConsole(snap) {
  const actions = asArray(snap.operation_console?.actions);
  return el("div", { class: "operation-console" }, [
    el("h3", { text: "操作控制台" }),
    el("p", { class: "muted", text: snap.operation_console?.guidance || "暂无操作指引。" }),
    el("div", { class: "command-grid" }, actions.map((action) => cmd(displayCommandLabel(action), () => runOperation(action), !action.enabled))),
  ]);
}

function renderGraphResult() {
  if (!state.detail) return null;
  return el("div", { class: "operation-console" }, [
    el("h3", { text: "最新结果" }),
    el("pre", { class: "code tall", text: pretty(state.detail) }),
  ]);
}

function renderTasks() {
  if (!state.project) return emptyState("未选择项目", "任务中心需要先选择项目。");
  const snap = snapshot();
  const assignments = asArray(snap.task_assignments);
  const taskDetail = state.context || state.detail;
  return el("div", { class: "two-pane" }, [
    el("section", { class: "panel" }, [
      sectionTitle("任务中心", "领取、完成、失败、心跳、释放、清理"),
      el("div", { class: "form-row" }, [
        input("智能体 ID", "task-agent", "agent-backend"),
        input("领取令牌", "task-token", ""),
        input("结果摘要", "task-summary", "已通过控制台完成。"),
      ]),
      table(["任务分配", "角色", "状态", "工作项", "操作"], assignments.map((assignment) => [
        assignment.id,
        assignment.role_label || assignment.role,
        badge(assignment.status_label || assignment.status, tone(assignment.status)),
        assignment.workitem_id,
        el("div", { class: "row-actions" }, [
          cmd("上下文", () => loadTaskContext(assignment.id)),
          cmd("智能体", () => loadTaskAgents(assignment.id)),
          cmd("领取", () => claimTask(assignment.id)),
          cmd("完成", () => completeTask(assignment.id)),
          cmd("失败", () => failTask(assignment.id)),
          cmd("心跳", () => heartbeatTask(assignment.id)),
          cmd("释放", () => releaseTask(assignment.id)),
        ]),
      ])),
    ]),
    el("aside", { class: "panel" }, [
      sectionTitle("任务详情", "上下文和 API 结果"),
      el("div", { class: "command-stack" }, [cmd("领取下一项", claimNextTask), cmd("批量领取", claimBatch), cmd("释放已过期", releaseExpired), cmd("清理任务", sweepTasks)]),
      taskDetail ? el("pre", { class: "code tall", text: typeof taskDetail === "string" ? taskDetail : pretty(taskDetail) }) : el("p", { class: "muted", text: "选择任务上下文或执行任务操作。" }),
    ]),
  ]);
}

function renderAgents() {
  if (!state.project) return emptyState("未选择项目", "智能体列表需要先选择项目。");
  const snap = snapshot();
  const agents = [...asArray(snap.project_agents), ...asArray(snap.activation_nodes).filter((node) => !asArray(snap.project_agents).some((agent) => agent.role === node.role))];
  return el("div", { class: "two-pane" }, [
    el("section", { class: "panel" }, [
      sectionTitle("智能体", "动态激活、可领取任务、角色范围"),
      el("div", { class: "card-grid" }, agents.map((agent) => card(agent.role_label || agent.role, [
        metric("智能体 ID", agent.agent_id || "-"),
        metric("状态", agent.status_label || (agent.active ? "活跃" : "空闲")),
        el("p", { class: "muted", text: agent.mission || agent.reason || "暂无任务记录。" }),
        cmd("领取", () => claimAgentTask(agent.agent_id || valueOf("agent-id") || "agent-backend")),
      ]))),
    ]),
    el("aside", { class: "panel" }, [
      sectionTitle("智能体任务 API", "按智能体查看和领取"),
      input("智能体 ID", "agent-id", "agent-backend"),
      el("div", { class: "command-stack" }, [cmd("查看可领取", listAgentTasks), cmd("领取任务", () => claimAgentTask())]),
      state.detail ? el("pre", { class: "code tall", text: pretty(state.detail) }) : null,
    ]),
  ]);
}

function renderReview() {
  if (!state.project) return emptyState("未选择项目", "评审需要先选择项目。");
  const snap = snapshot();
  return el("div", { class: "two-pane" }, [
    el("section", { class: "panel" }, [
      sectionTitle("评审与产物", "人工控制、审批、生成证据"),
      el("div", { class: "command-grid" }, humanActions.map(([action, label]) => cmd(label, () => humanAction(action)))),
      table(["产物", "类型", "版本", "来源", "操作"], asArray(snap.artifacts).map((artifact) => [
        artifact.title || artifact.id,
        artifact.kind,
        artifact.version,
        artifact.source_backend || "-",
        cmd("打开", () => loadArtifact(artifact.id)),
      ])),
    ]),
    el("aside", { class: "panel" }, [
      sectionTitle("选中产物", "内容与载荷"),
      state.detail ? renderReviewDetail(state.detail) : el("p", { class: "muted", text: "打开产物或执行评审操作。" }),
    ]),
  ]);
}

function renderReviewDetail(detail) {
  const artifact = detail?.artifact;
  if (!artifact) return el("pre", { class: "code tall", text: pretty(detail) });
  return el("div", { class: "artifact-detail" }, [
    metric("标题", artifact.title || artifact.id),
    metric("类型", artifact.kind || "-"),
    metric("来源", artifact.source_backend || "-"),
    metric("版本", artifact.version ?? "-"),
    el("pre", { class: "code tall", text: detail.content || artifact.content || pretty(detail) }),
  ]);
}

function renderLogs() {
  const snap = snapshot();
  return el("div", { class: "two-pane" }, [
    el("section", { class: "panel" }, [
      sectionTitle("运行时与事件", "实时流、路由、执行记录"),
      logList("最近事件", asArray(snap.recent_events_tail)),
      logList("路由", asArray(snap.route_lines)),
    ]),
    el("aside", { class: "panel" }, [
      sectionTitle("执行记录", "最新执行记录"),
      ...asArray(snap.executions).slice(-12).reverse().map((item) => el("div", { class: `log-line ${tone(item.status)}`, text: `${item.workitem_id || "-"} ${item.agent_label || item.agent_role || ""} ${item.status_label || item.status || ""}` })),
    ]),
  ]);
}

function renderSettings() {
  if (state.demoMode) return renderDemoSettings();
  return el("div", { class: "settings" }, [
    card("API 连接", [input("后端 URL", "api-base", api.baseUrl), cmd("保存 API", saveApiBase)]),
    renderBackendStatusCard(),
    card("实时能力就绪度", [
      ...renderCapabilityRows(liveCapabilityMatrix()),
      el("p", { class: "muted", text: "展示真实智能体执行前，请先运行诊断和 LLM 预检。" }),
    ]),
    settingsCard("执行配置", "execution", () => api.executionSettings(), (payload) => api.saveExecutionSettings(payload)),
    settingsCard("CLI", "cli", () => api.cliSettings(), (payload) => api.saveCliSettings(payload.config || payload)),
    card("LLM", [
      cmd("加载", async () => { await busy(async () => { state.settings.llm = await api.llmSettings(); }); }),
      cmd("保存", async () => { await busy(async () => { state.settings.llm = (await api.saveLlmSettings(state.settings.llm?.config || state.settings.llm || {})).config; }); }),
      cmd("预检", async () => { await busy(async () => { state.settings.llmPreflight = await api.llmPreflight(state.settings.llm || {}); }); }),
      state.settings.llm ? el("pre", { class: "code", text: pretty(state.settings.llm) }) : null,
      state.settings.llmPreflight ? el("pre", { class: "code", text: pretty(state.settings.llmPreflight) }) : null,
    ]),
    card("诊断", [
      cmd("加载", () => loadDiagnostics(false)),
      cmd("探测", () => loadDiagnostics(true)),
      state.diagnostics ? el("pre", { class: "code", text: pretty(state.diagnostics) }) : null,
    ]),
  ]);
}

function saveApiBase() {
  api.setBaseUrl(valueOf("api-base"));
  state.apiStatus = null;
  refreshAll();
}

function renderDemoSettings() {
  const settings = demoSettingsPayload();
  return el("div", { class: "settings" }, [
    card("演示运行时", [
      metric("模式", "离线夹具"),
      metric("是否需要后端", "否"),
      metric("项目", "demo-api-delivery"),
      el("div", { class: "command-grid" }, [cmd("重置演示", resetDemoMode), cmd("显示诊断", () => loadDiagnostics(true)), cmd("LLM 预检", demoLlmPreflightAction)]),
    ]),
    card("能力对齐", [
      ...renderCapabilityRows(demoCapabilityMatrix()),
      el("p", { class: "muted", text: "演示操作是确定性的，但每个可见区域都映射到实时 API 或运行时依赖。" }),
    ]),
    card("API 连接", [
      input("后端 URL", "api-base", api.baseUrl),
      el("p", { class: "muted", text: "演示模式会显示该值，但不会调用后端。" }),
      el("div", { class: "status-strip warn" }, [
        badge("已隔离", "warn"),
        el("span", { text: "切换到实时模式前，后端检查保持禁用。" }),
      ]),
    ]),
    card("执行配置", [
      metric("运行配置", settings.execution.config.run_profile),
      metric("静态冒烟", settings.execution.config.static_smoke ? "已启用" : "已禁用"),
      metric("后端检查", settings.execution.config.backend_checks),
      el("pre", { class: "code", text: pretty(settings.execution) }),
    ]),
    card("CLI 与 LLM", [
      metric("Agent CLI", settings.cli.config.agent_cli_required ? "必需" : "非必需"),
      metric("LLM 服务商", settings.llm.config.provider),
      metric("兜底", settings.cli.config.fallback),
      el("pre", { class: "code", text: pretty({ cli: settings.cli, llm: settings.llm }) }),
    ]),
    card("诊断", [
      state.diagnostics ? el("pre", { class: "code", text: pretty(state.diagnostics) }) : el("p", { class: "muted", text: "点击显示诊断以渲染本地演示就绪证据。" }),
    ]),
  ]);
}

function renderBackendStatusCard() {
  const status = state.apiStatus;
  const online = status?.ok === true;
  const known = Boolean(status);
  return card("后端状态", [
    metric("API URL", api.baseUrl),
    metric("状态", known ? (online ? "在线" : "不可达") : "未检查"),
    status?.run_profile ? metric("运行配置", status.run_profile) : null,
    typeof status?.project_count === "number" ? metric("项目数", status.project_count) : null,
    typeof status?.real_executor_ready === "boolean" ? metric("执行器就绪", status.real_executor_ready ? "是" : "需要配置") : null,
    status?.error ? el("div", { class: "status-strip bad" }, [badge("错误", "bad"), el("span", { text: status.error })]) : null,
    asArray(status?.warnings).length ? el("div", { class: "status-strip warn" }, [badge("警告", "warn"), el("span", { text: asArray(status.warnings).join("; ") })]) : null,
    el("div", { class: "command-grid" }, [cmd("检查后端", checkBackendStatus), cmd("诊断", () => loadDiagnostics(false))]),
  ]);
}

function liveCapabilityMatrix() {
  return [
    {
      area: "依赖图",
      demo: "n/a",
      live: state.selectedProjectId ? "已从选中项目快照加载。" : "请选择或创建项目以加载图数据。",
      status: state.selectedProjectId ? "就绪" : "需要项目",
    },
    {
      area: "任务中心",
      demo: "n/a",
      live: state.selectedProjectId ? "任务领取、释放、心跳、清理和上下文 API 可用。" : "需要先选择项目。",
      status: state.selectedProjectId ? "就绪" : "需要项目",
    },
    {
      area: "智能体",
      demo: "n/a",
      live: "智能体列表来自项目状态；外部执行依赖 CLI/LLM 配置。",
      status: "检查诊断",
    },
    {
      area: "评审产物",
      demo: "n/a",
      live: state.selectedProjectId ? "已存储产物和人工控制 API 可用。" : "需要先选择项目。",
      status: state.selectedProjectId ? "就绪" : "需要项目",
    },
    {
      area: "Todo API",
      demo: "n/a",
      live: "由平台 Todo API 端点和会话存储支撑。",
      status: "就绪",
    },
    {
      area: "外部执行",
      demo: "n/a",
      live: "真实运行前需要 Agent CLI 和/或 LLM 设置通过诊断。",
      status: "需要配置",
    },
  ];
}

function renderCapabilityRows(items) {
  return [
    el("div", { class: "capability-list" }, items.map((item) => el("div", { class: `capability-item ${tone(item.status)}` }, [
      el("div", { class: "capability-head" }, [
        el("strong", { text: item.area }),
        badge(item.status, tone(item.status)),
      ]),
      item.demo && item.demo !== "n/a" ? el("p", { text: `演示：${item.demo}` }) : null,
      el("p", { text: `实时：${item.live}` }),
    ]))),
  ];
}

function renderTodos() {
  const todosPayload = state.demoMode ? ensureDemoTodos() : state.todos;
  const statsPayload = state.todoStats;
  return el("div", { class: "two-pane" }, [
    el("section", { class: "panel" }, [
      sectionTitle("Todo API", "辅助 CRUD 覆盖"),
      el("div", { class: "form-row" }, [input("标题", "todo-title", "评审依赖图"), input("内容", "todo-content", "检查 API 覆盖")]),
      el("div", { class: "command-grid" }, [cmd("创建", createTodo), cmd("刷新", loadTodos)]),
      table(["标题", "状态", "内容", "操作"], asArray(todosPayload?.todos).map((todo) => [
        todo.title,
        todo.completed ? "已完成" : "未完成",
        short(todo.content, 80),
        el("div", { class: "row-actions" }, [cmd("查看", () => loadTodo(todo.id)), cmd(todo.completed ? "重开" : "完成", () => updateTodo(todo.id, { completed: !todo.completed })), cmd("删除", () => deleteTodo(todo.id))]),
      ])),
    ]),
    el("aside", { class: "panel" }, [
      sectionTitle("Todo 详情", `已加载 ${todosPayload?.todos?.length || 0} 条 / 共 ${statsPayload?.total ?? 0} 条`),
      renderTodoStats(statsPayload),
      state.todoDetail ? el("pre", { class: "code tall", text: pretty(state.todoDetail) }) : null,
    ]),
  ]);
}

function renderTodoStats(stats) {
  return el("div", { class: "stats-grid" }, [
    metric("总数", stats?.total ?? 0),
    metric("已完成", stats?.completed ?? 0),
    metric("未完成", stats?.active ?? 0),
  ]);
}

function settingsCard(title, key, load, save) {
  return card(title, [
    cmd("加载", async () => { await busy(async () => { state.settings[key] = await load(); }); }),
    cmd("保存", async () => {
      const parsed = parseJsonEditor(`settings-${key}`, state.settings[key] || {});
      await busy(async () => { state.settings[key] = await save(parsed); });
    }),
    state.settings[key] ? jsonEditor(`settings-${key}`, state.settings[key]) : el("p", { class: "muted", text: "加载设置后查看载荷。" }),
  ]);
}

function jsonEditor(id, value) {
  return el("textarea", { id, class: "json-editor" }, [pretty(value)]);
}

function parseJsonEditor(id, fallback) {
  const text = document.getElementById(id)?.value;
  if (!text) return fallback;
  try {
    return JSON.parse(text);
  } catch (error) {
    throw new Error(`${id} 中的 JSON 无效：${error.message}`);
  }
}

function table(headers, rows) {
  return el("table", {}, [
    el("thead", {}, [el("tr", {}, headers.map((head) => el("th", { text: head })))]),
    el("tbody", {}, rows.map((row) => el("tr", {}, row.map((cell) => el("td", {}, [cell instanceof Node ? cell : String(cell ?? "")]))))),
  ]);
}

function card(title, body) {
  return el("section", { class: "panel" }, [sectionTitle(title), ...body]);
}
function sectionTitle(title, subtitle = "") {
  return el("div", { class: "section-title" }, [el("h2", { text: title }), subtitle ? el("p", { text: subtitle }) : null]);
}
function metric(label, value) {
  return el("div", { class: "metric" }, [el("span", { text: label }), el("strong", { text: String(value ?? "-") })]);
}
function badge(text, kind = "info") {
  return el("span", { class: `badge ${kind}`, text: String(text || "-") });
}
function cmd(label, action, disabled = false) {
  return el("button", { class: "cmd", onclick: action, disabled: disabled ? "true" : null, title: label }, [label]);
}
function input(label, id, value = "") {
  return el("label", { class: "field" }, [el("span", { text: label }), el("input", { id, value })]);
}
function emptyState(title, body) {
  return el("section", { class: "empty-state" }, [
    el("h1", { text: title }),
    el("p", { text: body }),
    el("div", { class: "empty-actions" }, [
      cmd("创建项目", openCreateProject),
      state.demoMode ? null : cmd("加载演示", enableDemoMode),
    ]),
  ]);
}
function logList(title, lines) {
  return el("div", { class: "log-group" }, [el("h3", { text: title }), ...lines.slice(-14).map((line) => el("div", { class: "log-line", text: short(line, 160) }))]);
}

function displayNodeType(type) {
  return nodeTypeLabels[type] || type || "";
}

function displayCommandLabel(action) {
  if (!action) return "";
  return action.id ? ({
    "claim-next": "领取下一项",
    sweep: "清理任务",
    approve: "批准",
  }[action.id] || commandLabels[action.label] || action.label) : (commandLabels[action.label] || action.label || "");
}

function displayStatus(value) {
  const labels = {
    active: "活跃",
    blocked: "阻塞",
    claimable: "可领取",
    done: "已完成",
    failed: "失败",
    idle: "空闲",
    pending: "待处理",
    queued: "排队中",
    ready: "就绪",
    running: "运行中",
    success: "成功",
  };
  return labels[value] || value || "-";
}

async function busy(action) {
  state.loading = true;
  state.error = "";
  try {
    await action();
  } catch (error) {
    state.error = error.message;
    state.toast = "";
  } finally {
    state.loading = false;
    renderShell();
  }
}

async function openCreateProject() {
  if (state.demoMode) {
    state.toast = "演示模式下不能创建项目。";
    renderShell();
    return;
  }
  const requirement = prompt("需求", "构建 Todo 项后端 REST API，包含创建、列表、更新、删除和统计端点。");
  if (!requirement) return;
  const projectRoot = prompt("项目根目录", "");
  await busy(async () => {
    const payload = await api.createProject({ requirement, project_root: projectRoot || "" });
    state.selectedProjectId = payload.project_id || payload.snapshot?.project_id;
    await loadProject(state.selectedProjectId, true);
  });
}

async function projectCommand(action) {
  if (!state.selectedProjectId) return;
  if (state.demoMode) {
    state.project = advanceDemoProject(state.project, action);
    state.toast = `已对演示项目执行${action === "run" ? "运行" : "推进"}。`;
    renderShell();
    return;
  }
  await busy(async () => { await api[action](state.selectedProjectId); await loadProject(state.selectedProjectId, true); });
}

async function runOperation(action) {
  if (!state.selectedProjectId || !action.enabled) return;
  if (!action.api_method || !action.api_path) return;
  if (state.demoMode) {
    state.detail = demoOperationResult(action.label);
    if (action.id === "claim-next") state.project = advanceDemoProject(state.project, "step");
    state.toast = `${displayCommandLabel(action)}已记录。`;
    renderShell();
    return;
  }
  await busy(async () => {
    const method = action.api_method.toUpperCase();
    const options = { method };
    if (!["GET", "HEAD"].includes(method)) options.body = defaultPayload(action);
    state.detail = await api.request(action.api_path, options);
    await loadProject(state.selectedProjectId, true);
  });
}

async function checkBackendStatus() {
  if (state.demoMode) {
    state.apiStatus = {
      ok: true,
      api: "demo-isolated",
      run_profile: "offline-demo",
      warnings: ["演示模式不会调用后端。"],
    };
    state.toast = "演示模式已隔离后端检查。";
    renderShell();
    return;
  }
  state.loading = true;
  state.error = "";
  try {
    state.apiStatus = await api.request("/api/status");
    state.toast = "后端状态已检查。";
  } catch (error) {
    state.apiStatus = { ok: false, api: "unreachable", error: error.message, warnings: [] };
    state.toast = "";
  } finally {
    state.loading = false;
    renderShell();
  }
}

function defaultPayload(action) {
  if (action.category === "human_control") return { actor: "operator", reason: action.reason || action.label };
  if (action.id?.includes("sweep")) return { stale_after_seconds: 3600 };
  return { agent_id: "agent-backend", release_reason: action.reason || action.label };
}
async function loadTaskContext(id) { if (demoAction(() => setTaskContext(demoTaskContext(id)))) return; await busy(async () => { setTaskContext(await api.taskContext(state.selectedProjectId, id, { format: "markdown", include_content: true })); }); }
async function loadTaskAgents(id) { if (demoAction(() => setTaskContext({ assignment_id: id, agents: snapshot().project_agents || [] }))) return; await busy(async () => { setTaskContext(await api.taskAgents(state.selectedProjectId, id)); }); }
async function claimTask(id) { if (demoAction(() => setTaskDetail(demoOperationResult(`领取 ${id}`)))) return; await busy(async () => { setTaskDetail(await api.claimTask(state.selectedProjectId, id, { agent_id: valueOf("task-agent") || "agent-backend", include_context: true, context_format: "markdown" })); await loadProject(state.selectedProjectId, true); }); }
async function claimNextTask() { if (demoAction(() => { setTaskDetail(demoOperationResult("领取下一项")); state.project = advanceDemoProject(state.project, "step"); })) return; await busy(async () => { setTaskDetail(await api.claimNext(state.selectedProjectId, { agent_id: valueOf("task-agent") || "agent-backend", include_context: true, context_format: "markdown" })); await loadProject(state.selectedProjectId, true); }); }
async function claimBatch() { if (demoAction(() => setTaskDetail({ ...demoOperationResult("批量领取"), claimed: snapshot().task_assignments?.slice(0, 2) || [] }))) return; await busy(async () => { setTaskDetail(await api.claimBatch(state.selectedProjectId, { agent_id: valueOf("task-agent") || "agent-backend", limit: 2 })); await loadProject(state.selectedProjectId, true); }); }
async function completeTask(id) { if (demoAction(() => { setTaskDetail(demoOperationResult(`完成 ${id}`)); state.project = advanceDemoProject(state.project, "step"); })) return; await busy(async () => { setTaskDetail(await api.completeTask(state.selectedProjectId, id, { agent_id: valueOf("task-agent") || undefined, claim_token: valueOf("task-token"), result_summary: valueOf("task-summary") })); await loadProject(state.selectedProjectId, true); }); }
async function failTask(id) { if (demoAction(() => setTaskDetail(demoOperationResult(`标记失败 ${id}`)))) return; await busy(async () => { setTaskDetail(await api.failTask(state.selectedProjectId, id, { agent_id: valueOf("task-agent") || undefined, claim_token: valueOf("task-token"), result_summary: valueOf("task-summary"), blocked_reason: valueOf("task-summary") || "手动失败" })); await loadProject(state.selectedProjectId, true); }); }
async function heartbeatTask(id) { if (demoAction(() => setTaskDetail(demoOperationResult(`心跳 ${id}`)))) return; await busy(async () => { setTaskDetail(await api.heartbeatTask(state.selectedProjectId, id, { agent_id: valueOf("task-agent") || undefined, claim_token: valueOf("task-token") })); await loadProject(state.selectedProjectId, true); }); }
async function releaseTask(id) { if (demoAction(() => setTaskDetail(demoOperationResult(`释放 ${id}`)))) return; await busy(async () => { setTaskDetail(await api.releaseTask(state.selectedProjectId, id, { agent_id: valueOf("task-agent") || undefined, claim_token: valueOf("task-token"), release_reason: "从控制台释放" })); await loadProject(state.selectedProjectId, true); }); }
async function releaseStale() { if (demoAction(() => setTaskDetail(demoOperationResult("释放过期任务")))) return; await busy(async () => { setTaskDetail(await api.releaseStale(state.selectedProjectId, { stale_after_seconds: 3600, release_reason: "过期清理" })); await loadProject(state.selectedProjectId, true); }); }
async function releaseExpired() { if (demoAction(() => setTaskDetail(demoOperationResult("释放已过期任务")))) return; await busy(async () => { setTaskDetail(await api.releaseExpired(state.selectedProjectId, { release_reason: "租约过期清理" })); await loadProject(state.selectedProjectId, true); }); }
async function sweepTasks() { if (demoAction(() => setTaskDetail(demoOperationResult("清理任务")))) return; await busy(async () => { setTaskDetail(await api.sweep(state.selectedProjectId, { stale_after_seconds: 3600 })); await loadProject(state.selectedProjectId, true); }); }
async function claimAgentTask(agentId = valueOf("agent-id") || "agent-backend") { if (demoAction(() => { state.detail = demoOperationResult(`智能体 ${agentId} 领取`); })) return; await busy(async () => { state.detail = await api.claimAgentTask(state.selectedProjectId, agentId, { agent_id: agentId, include_context: true, context_format: "json" }); await loadProject(state.selectedProjectId, true); }); }
async function listAgentTasks() { if (demoAction(() => { state.detail = { agent_id: valueOf("agent-id") || "agent-backend", tasks: snapshot().task_assignments || [] }; })) return; await busy(async () => { state.detail = await api.agentTasks(state.selectedProjectId, valueOf("agent-id") || "agent-backend", true); }); }
async function loadArtifact(id) { if (demoAction(() => { state.detail = demoArtifact(id); })) return; await busy(async () => { state.detail = await api.artifact(state.selectedProjectId, id); }); }
async function loadHumanControl() { if (demoAction(() => { state.detail = snapshot().human_control || {}; })) return; await busy(async () => { state.detail = await api.humanControl(state.selectedProjectId); }); }
async function loadLiveState() { if (demoAction(() => { state.detail = state.project; })) return; await busy(async () => { state.detail = await api.live(state.selectedProjectId); }); }
async function humanAction(action) { if (demoAction(() => { state.detail = demoOperationResult(`人工操作 ${action}`); })) return; await busy(async () => { state.detail = await api.humanAction(state.selectedProjectId, action, { actor: "operator", reason: `frontend ${action}` }); await loadProject(state.selectedProjectId, true); }); }
async function loadDiagnostics(probe) { if (demoAction(() => { state.diagnostics = demoDiagnostics(probe); })) return; await busy(async () => { state.diagnostics = await api.diagnostics({ probe_cli: probe, probe_llm: probe, preflight_llm: probe }); }); }
async function loadTodos() { if (demoAction(() => { resetDemoTodos(); state.todoDetail = null; })) return; await busy(async () => { state.todos = await api.todos(); state.todoStats = await api.todoStats(); }); }
async function createTodo() { const payload = { title: valueOf("todo-title"), content: valueOf("todo-content") }; if (demoAction(() => createDemoTodo(payload))) return; await busy(async () => { await api.createTodo(payload); state.todos = await api.todos(); state.todoStats = await api.todoStats(); }); }
async function loadTodo(id) { if (demoAction(() => { state.todoDetail = demoTodoDetailFromState(id); })) return; await busy(async () => { state.todoDetail = await api.getTodo(id); }); }
async function updateTodo(id, payload) { if (demoAction(() => updateDemoTodo(id, payload))) return; await busy(async () => { await api.updateTodo(id, payload); state.todos = await api.todos(); state.todoStats = await api.todoStats(); }); }
async function deleteTodo(id) { if (demoAction(() => deleteDemoTodo(id))) return; await busy(async () => { await api.deleteTodo(id); state.todos = await api.todos(); state.todoStats = await api.todoStats(); }); }

function demoAction(action) {
  if (!state.demoMode) return false;
  action();
  state.toast = "演示状态已在本地更新。";
  renderShell();
  return true;
}

function resetDemoMode() {
  loadDemoProject(0, true);
  state.context = null;
  state.detail = null;
  state.diagnostics = null;
  state.toast = "演示模式已重置。";
  renderShell();
}

function demoLlmPreflightAction() {
  state.diagnostics = demoLlmPreflight();
  state.toast = "演示 LLM 预检已在本地渲染。";
  renderShell();
}

function setTaskContext(value) {
  state.context = value;
  state.detail = null;
}

function setTaskDetail(value) {
  state.detail = value;
  state.context = null;
}

function ensureDemoTodos(reset = false) {
  if (reset || !state.todos?.todos) resetDemoTodos();
  return state.todos;
}

function resetDemoTodos() {
  state.todos = demoTodos();
  state.todoStats = demoTodoStatsFromItems(state.todos.todos);
}

function createDemoTodo(payload) {
  const todos = ensureDemoTodos().todos;
  const nextId = todos.reduce((max, todo) => Math.max(max, Number(todo.id) || 0), 0) + 1;
  const todo = {
    id: nextId,
    title: payload.title?.trim() || "未命名演示 Todo",
    content: payload.content?.trim() || "已在演示模式本地创建。",
    completed: false,
  };
  todos.unshift(todo);
  state.todoStats = demoTodoStatsFromItems(todos);
  state.todoDetail = { todo: { ...todo }, mode: "demo", action: "created" };
}

function updateDemoTodo(id, payload) {
  const todos = ensureDemoTodos().todos;
  const todo = todos.find((item) => Number(item.id) === Number(id));
  if (!todo) {
    state.todoDetail = demoOperationResult(`更新缺失 Todo ${id}`);
    return;
  }
  Object.assign(todo, payload);
  state.todoStats = demoTodoStatsFromItems(todos);
  state.todoDetail = { todo: { ...todo }, mode: "demo", action: "updated" };
}

function deleteDemoTodo(id) {
  const todos = ensureDemoTodos().todos;
  const index = todos.findIndex((item) => Number(item.id) === Number(id));
  if (index < 0) {
    state.todoDetail = demoOperationResult(`删除缺失 Todo ${id}`);
    return;
  }
  const [todo] = todos.splice(index, 1);
  state.todoStats = demoTodoStatsFromItems(todos);
  state.todoDetail = { todo: { ...todo }, mode: "demo", action: "deleted" };
}

function demoTodoDetailFromState(id) {
  const todos = ensureDemoTodos().todos;
  const todo = todos.find((item) => Number(item.id) === Number(id));
  return todo ? { todo: { ...todo }, mode: "demo" } : demoTodoDetail(id);
}

function demoTodoStatsFromItems(todos) {
  const completed = asArray(todos).filter((todo) => todo.completed).length;
  return {
    total: asArray(todos).length,
    completed,
    active: asArray(todos).length - completed,
  };
}

function jitter(seed, range) {
  const text = String(seed || "");
  let value = 0;
  for (let i = 0; i < text.length; i += 1) value = (value * 31 + text.charCodeAt(i)) % 997;
  return Math.round((value / 997 - 0.5) * range);
}
