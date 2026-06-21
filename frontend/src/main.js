import { ApiClient, getApiBase } from "./api.js";
import { advanceDemoProject, createDemoProject, demoArtifact, demoCapabilityMatrix, demoDiagnostics, demoLlmPreflight, demoOperationResult, demoProjects, demoSettingsPayload, demoTaskContext, demoTodoDetail, demoTodos } from "./demo.js";
import { asArray, el, empty, pretty, short, tone, valueOf } from "./utils.js";

const api = new ApiClient();
const app = document.getElementById("app");

const nav = [
  ["graph", "Dependency Graph"],
  ["tasks", "Tasks"],
  ["agents", "Agents"],
  ["review", "Review"],
  ["logs", "Logs"],
  ["settings", "Settings"],
  ["todos", "Todos"],
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
      el("div", { class: "brand" }, [el("strong", { text: "CONDUCTOR" }), el("small", { text: "Agent control plane" })]),
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
        el("option", { value: "", text: "Select project" }),
        ...displayProjects().map((project) => el("option", {
          value: project.project_id,
          selected: project.project_id === state.selectedProjectId,
          text: `${project.status_label || project.status || "-"} / ${short(project.goal || project.project_id, 52)}`,
        })),
      ]),
      badge(snap.project_status_label || snap.project_status || "no project", tone(snap.project_status)),
      state.demoMode ? badge("demo", "warn") : null,
      state.demoMode ? badge(demoProgressLabel(snap), "info") : null,
      badge(api.baseUrl, "info"),
    ]),
    el("div", { class: "topbar-actions" }, [
      cmd(state.demoMode ? "Live" : "Demo", toggleDemoMode),
      cmd("New", openCreateProject),
      cmd("Step", () => projectCommand("step"), !state.selectedProjectId),
      cmd("Run", () => projectCommand("run"), !state.selectedProjectId),
      cmd("Refresh", refreshAll),
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
  state.toast = state.demoMode ? "Demo mode loaded." : "";
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
  if (step >= 2) return "path 4/4";
  if (step >= 1) return "path 3/4";
  return "path 1/4";
}

function render() {
  const view = document.getElementById("view");
  if (!view) return;
  empty(view);
  if (state.error) view.append(renderErrorBanner(state.error));
  if (state.toast) view.append(el("div", { class: "banner good", text: state.toast }));
  if (state.loading) view.append(el("div", { class: "banner info", text: "Loading..." }));
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
  if (!state.project) return emptyState("No project selected", "Create, select, or load the demo project to inspect the dependency graph.");
  const snap = snapshot();
  const graph = buildGraph(snap);
  const selected = state.node ? graph.nodes.find((node) => node.id === state.node) : graph.nodes[0];
  return el("div", { class: "graph-layout" }, [
    el("aside", { class: "risk-panel" }, [
      sectionTitle("Risk Control", "Dependency health and operator attention"),
      metric("Risk", snap.run_audit?.risk_level_label || snap.run_audit?.risk_level || "-"),
      metric("Readiness", `${snap.run_audit?.delivery_readiness_status_label || "-"} / ${snap.run_audit?.delivery_readiness_score || 0}`),
      metric("Claimable", snap.task_center_summary?.claimable || 0),
      metric("Blocked", asArray(snap.blockers).length),
      el("div", { class: "command-stack" }, [
        cmd("Live State", () => loadLiveState()),
        cmd("Claim Next", () => claimNextTask()),
        cmd("Sweep", () => sweepTasks()),
        cmd("Release Stale", () => releaseStale()),
        cmd("Human Gate", () => loadHumanControl()),
      ]),
      state.demoMode ? renderDemoRunbook(snap) : null,
    ]),
    el("section", { class: "graph-canvas" }, [
      el("div", { class: "graph-toolbar" }, [
        sectionTitle("Dependency Graph", short(snap.project_goal || "Current project", 92)),
        el("div", { class: "legend" }, ["good", "warn", "bad", "info"].map((name) => badge(name, name))),
      ]),
      renderSvgGraph(graph),
    ]),
    el("aside", { class: "inspector" }, [
      sectionTitle(selected?.label || "Inspector", selected?.type || "Select a graph node"),
      selected ? renderNodeInspector(selected) : el("p", { class: "muted", text: "No node selected." }),
      renderOperationConsole(snap),
      renderGraphResult(),
    ]),
  ]);
}

function renderErrorBanner(message) {
  return el("div", { class: "banner bad action-banner" }, [
    el("div", { class: "banner-copy" }, [
      el("strong", { text: "Live API unavailable" }),
      el("span", { text: message }),
      el("small", { text: "Load the offline demo for a presentation fallback, or open Settings to check the backend URL." }),
    ]),
    state.demoMode ? null : cmd("Load Demo", enableDemoMode),
    state.demoMode ? null : cmd("Settings", () => navigate("settings")),
  ]);
}

function enableDemoMode() {
  state.demoMode = true;
  localStorage.setItem("conductor.demoMode", "1");
  state.toast = "Demo mode loaded.";
  loadDemoProject(0, true);
  renderShell();
}

function renderDemoRunbook(snap) {
  const step = Number(snap.demo_step || 0);
  const items = [
    {
      title: "1. Inspect graph",
      body: "Show stages, WorkItems, assignments, agents, artifacts and human gate.",
      done: true,
    },
    {
      title: "2. Step validation",
      body: "Click Step. Expect risk Low, readiness At Risk / 92 and blockers 0.",
      done: step >= 1,
    },
    {
      title: "3. Run delivery",
      body: "Click Run. Expect project Ready, readiness Ready / 96 and Approve enabled.",
      done: step >= 2,
    },
    {
      title: "4. Review evidence",
      body: "Open Review, inspect FastAPI SQLite Implementation and API Contract Validation.",
      done: step >= 2,
    },
  ];
  return el("div", { class: "demo-runbook" }, [
    el("h3", { text: "Demo Runbook" }),
    el("p", { text: step >= 2 ? "Delivery story is ready for the Review tab." : "Follow these steps during the presentation." }),
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
    nodes.push({ id: `stage:${stage}`, type: "stage", label: stage.toUpperCase(), x: stageX[stage], y: 70, status: index <= stages.indexOf(snap.current_stage || "requirement") ? "running" : "pending" });
    if (index) links.push({ from: `stage:${stages[index - 1]}`, to: `stage:${stage}`, status: "info" });
  });
  for (const item of asArray(snap.workitems)) {
    const stage = item.stage || "delivery";
    nodes.push({ id: `work:${item.id}`, type: "task", label: item.kind_label || item.kind || item.id, sub: item.id, x: (stageX[stage] || 520) + jitter(item.id, 42), y: (stageY[stage] || 300) + jitter(item.description, 26), status: item.status, raw: item });
    links.push({ from: `stage:${stage}`, to: `work:${item.id}`, status: item.status });
  }
  for (const assignment of asArray(snap.task_assignments)) {
    nodes.push({ id: `assign:${assignment.id}`, type: "lease", label: assignment.role_label || assignment.role || "Agent Task", sub: assignment.status_label || assignment.status, x: 500 + jitter(assignment.id, 260), y: 170 + jitter(assignment.role, 260), status: assignment.status, raw: assignment });
    links.push({ from: `work:${assignment.workitem_id}`, to: `assign:${assignment.id}`, status: assignment.status });
  }
  for (const agent of asArray(snap.project_agents).slice(0, 8)) {
    nodes.push({ id: `agent:${agent.agent_id}`, type: "agent", label: agent.role_label || agent.role, sub: agent.agent_id, x: 120 + jitter(agent.agent_id, 760), y: 545, status: "ready", raw: agent });
  }
  for (const artifact of asArray(snap.artifacts).slice(-8)) {
    nodes.push({ id: `artifact:${artifact.id}`, type: "artifact", label: artifact.title || artifact.kind, sub: artifact.kind, x: 760 + jitter(artifact.id, 130), y: 150 + jitter(artifact.title, 300), status: "ready", raw: artifact });
    if (artifact.workitem_id) links.push({ from: `work:${artifact.workitem_id}`, to: `artifact:${artifact.id}`, status: "good" });
  }
  nodes.push({ id: "gate:human", type: "human gate", label: "Blocked Gate", sub: snap.human_control?.active ? snap.human_control.hold_reason : "operator ready", x: 910, y: 350, status: snap.human_control?.active ? "blocked" : "ready", raw: snap.human_control });
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
    metric("Type", node.type),
    metric("Status", node.status || "-"),
    node.raw ? el("pre", { class: "code", text: pretty(node.raw) }) : null,
  ]);
}

function renderOperationConsole(snap) {
  const actions = asArray(snap.operation_console?.actions);
  return el("div", { class: "operation-console" }, [
    el("h3", { text: "Operation Console" }),
    el("p", { class: "muted", text: snap.operation_console?.guidance || "No operator guidance." }),
    el("div", { class: "command-grid" }, actions.map((action) => cmd(action.label, () => runOperation(action), !action.enabled))),
  ]);
}

function renderGraphResult() {
  if (!state.detail) return null;
  return el("div", { class: "operation-console" }, [
    el("h3", { text: "Latest Result" }),
    el("pre", { class: "code tall", text: pretty(state.detail) }),
  ]);
}

function renderTasks() {
  if (!state.project) return emptyState("No project selected", "Task Center requires a project.");
  const snap = snapshot();
  const assignments = asArray(snap.task_assignments);
  const taskDetail = state.context || state.detail;
  return el("div", { class: "two-pane" }, [
    el("section", { class: "panel" }, [
      sectionTitle("Task Center", "Claim, complete, fail, heartbeat, release, sweep"),
      el("div", { class: "form-row" }, [
        input("Agent ID", "task-agent", "agent-backend"),
        input("Claim token", "task-token", ""),
        input("Result summary", "task-summary", "Completed through console."),
      ]),
      table(["Assignment", "Role", "Status", "Workitem", "Actions"], assignments.map((assignment) => [
        assignment.id,
        assignment.role_label || assignment.role,
        badge(assignment.status_label || assignment.status, tone(assignment.status)),
        assignment.workitem_id,
        el("div", { class: "row-actions" }, [
          cmd("Context", () => loadTaskContext(assignment.id)),
          cmd("Agents", () => loadTaskAgents(assignment.id)),
          cmd("Claim", () => claimTask(assignment.id)),
          cmd("Done", () => completeTask(assignment.id)),
          cmd("Fail", () => failTask(assignment.id)),
          cmd("Beat", () => heartbeatTask(assignment.id)),
          cmd("Release", () => releaseTask(assignment.id)),
        ]),
      ])),
    ]),
    el("aside", { class: "panel" }, [
      sectionTitle("Task Detail", "Context and API result"),
      el("div", { class: "command-stack" }, [cmd("Claim Next", claimNextTask), cmd("Batch Claim", claimBatch), cmd("Release Expired", releaseExpired), cmd("Sweep", sweepTasks)]),
      taskDetail ? el("pre", { class: "code tall", text: typeof taskDetail === "string" ? taskDetail : pretty(taskDetail) }) : el("p", { class: "muted", text: "Select a task context or run a task action." }),
    ]),
  ]);
}

function renderAgents() {
  if (!state.project) return emptyState("No project selected", "Agent roster requires a project.");
  const snap = snapshot();
  const agents = [...asArray(snap.project_agents), ...asArray(snap.activation_nodes).filter((node) => !asArray(snap.project_agents).some((agent) => agent.role === node.role))];
  return el("div", { class: "two-pane" }, [
    el("section", { class: "panel" }, [
      sectionTitle("Agents", "Dynamic activation, claimable tasks, role scope"),
      el("div", { class: "card-grid" }, agents.map((agent) => card(agent.role_label || agent.role, [
        metric("Agent ID", agent.agent_id || "-"),
        metric("Status", agent.status_label || (agent.active ? "active" : "idle")),
        el("p", { class: "muted", text: agent.mission || agent.reason || "No mission recorded." }),
        cmd("Claim", () => claimAgentTask(agent.agent_id || valueOf("agent-id") || "agent-backend")),
      ]))),
    ]),
    el("aside", { class: "panel" }, [
      sectionTitle("Agent Task API", "List and claim by agent"),
      input("Agent ID", "agent-id", "agent-backend"),
      el("div", { class: "command-stack" }, [cmd("List claimable", listAgentTasks), cmd("Claim task", () => claimAgentTask())]),
      state.detail ? el("pre", { class: "code tall", text: pretty(state.detail) }) : null,
    ]),
  ]);
}

function renderReview() {
  if (!state.project) return emptyState("No project selected", "Review requires a project.");
  const snap = snapshot();
  return el("div", { class: "two-pane" }, [
    el("section", { class: "panel" }, [
      sectionTitle("Review and Artifacts", "Human control, approvals, generated evidence"),
      el("div", { class: "command-grid" }, ["pause", "resume", "request-approval", "approve", "reject", "override"].map((action) => cmd(action, () => humanAction(action)))),
      table(["Artifact", "Kind", "Version", "Source", "Action"], asArray(snap.artifacts).map((artifact) => [
        artifact.title || artifact.id,
        artifact.kind,
        artifact.version,
        artifact.source_backend || "-",
        cmd("Open", () => loadArtifact(artifact.id)),
      ])),
    ]),
    el("aside", { class: "panel" }, [
      sectionTitle("Selected Artifact", "Content and payload"),
      state.detail ? el("pre", { class: "code tall", text: pretty(state.detail) }) : el("p", { class: "muted", text: "Open an artifact or run a review action." }),
    ]),
  ]);
}

function renderLogs() {
  const snap = snapshot();
  return el("div", { class: "two-pane" }, [
    el("section", { class: "panel" }, [
      sectionTitle("Runtime and Events", "Live stream, routes, executions"),
      logList("Recent Events", asArray(snap.recent_events_tail)),
      logList("Routes", asArray(snap.route_lines)),
    ]),
    el("aside", { class: "panel" }, [
      sectionTitle("Executions", "Latest execution records"),
      ...asArray(snap.executions).slice(-12).reverse().map((item) => el("div", { class: `log-line ${tone(item.status)}`, text: `${item.workitem_id || "-"} ${item.agent_label || item.agent_role || ""} ${item.status_label || item.status || ""}` })),
    ]),
  ]);
}

function renderSettings() {
  if (state.demoMode) return renderDemoSettings();
  return el("div", { class: "settings" }, [
    card("API Connection", [input("Backend URL", "api-base", api.baseUrl), cmd("Save API", saveApiBase)]),
    renderBackendStatusCard(),
    card("Live Capability Readiness", [
      ...renderCapabilityRows(liveCapabilityMatrix()),
      el("p", { class: "muted", text: "Run Diagnostics and LLM Preflight before presenting real Agent execution." }),
    ]),
    settingsCard("Execution", "execution", () => api.executionSettings(), (payload) => api.saveExecutionSettings(payload)),
    settingsCard("CLI", "cli", () => api.cliSettings(), (payload) => api.saveCliSettings(payload.config || payload)),
    card("LLM", [
      cmd("Load", async () => { await busy(async () => { state.settings.llm = await api.llmSettings(); }); }),
      cmd("Save", async () => { await busy(async () => { state.settings.llm = (await api.saveLlmSettings(state.settings.llm?.config || state.settings.llm || {})).config; }); }),
      cmd("Preflight", async () => { await busy(async () => { state.settings.llmPreflight = await api.llmPreflight(state.settings.llm || {}); }); }),
      state.settings.llm ? el("pre", { class: "code", text: pretty(state.settings.llm) }) : null,
      state.settings.llmPreflight ? el("pre", { class: "code", text: pretty(state.settings.llmPreflight) }) : null,
    ]),
    card("Diagnostics", [
      cmd("Load", () => loadDiagnostics(false)),
      cmd("Probe", () => loadDiagnostics(true)),
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
    card("Demo Runtime", [
      metric("Mode", "Offline fixture"),
      metric("Backend required", "No"),
      metric("Project", "demo-api-delivery"),
      el("div", { class: "command-grid" }, [cmd("Reset Demo", resetDemoMode), cmd("Show Diagnostics", () => loadDiagnostics(true)), cmd("LLM Preflight", demoLlmPreflightAction)]),
    ]),
    card("Capability Alignment", [
      ...renderCapabilityRows(demoCapabilityMatrix()),
      el("p", { class: "muted", text: "Demo actions are deterministic, but every visible area maps to a live API or runtime dependency." }),
    ]),
    card("API Connection", [
      input("Backend URL", "api-base", api.baseUrl),
      el("p", { class: "muted", text: "Demo mode keeps this value visible but does not call the backend." }),
      el("div", { class: "status-strip warn" }, [
        badge("isolated", "warn"),
        el("span", { text: "Backend checks are disabled until you switch to Live mode." }),
      ]),
    ]),
    card("Execution", [
      metric("Run profile", settings.execution.config.run_profile),
      metric("Static smoke", settings.execution.config.static_smoke ? "enabled" : "disabled"),
      metric("Backend checks", settings.execution.config.backend_checks),
      el("pre", { class: "code", text: pretty(settings.execution) }),
    ]),
    card("CLI and LLM", [
      metric("Agent CLI", settings.cli.config.agent_cli_required ? "required" : "not required"),
      metric("LLM provider", settings.llm.config.provider),
      metric("Fallback", settings.cli.config.fallback),
      el("pre", { class: "code", text: pretty({ cli: settings.cli, llm: settings.llm }) }),
    ]),
    card("Diagnostics", [
      state.diagnostics ? el("pre", { class: "code", text: pretty(state.diagnostics) }) : el("p", { class: "muted", text: "Use Show Diagnostics to render local demo readiness evidence." }),
    ]),
  ]);
}

function renderBackendStatusCard() {
  const status = state.apiStatus;
  const online = status?.ok === true;
  const known = Boolean(status);
  return card("Backend Status", [
    metric("API URL", api.baseUrl),
    metric("Status", known ? (online ? "Online" : "Unreachable") : "Not checked"),
    status?.run_profile ? metric("Run profile", status.run_profile) : null,
    typeof status?.project_count === "number" ? metric("Projects", status.project_count) : null,
    typeof status?.real_executor_ready === "boolean" ? metric("Executor ready", status.real_executor_ready ? "Yes" : "Needs setup") : null,
    status?.error ? el("div", { class: "status-strip bad" }, [badge("error", "bad"), el("span", { text: status.error })]) : null,
    asArray(status?.warnings).length ? el("div", { class: "status-strip warn" }, [badge("warnings", "warn"), el("span", { text: asArray(status.warnings).join("; ") })]) : null,
    el("div", { class: "command-grid" }, [cmd("Check Backend", checkBackendStatus), cmd("Diagnostics", () => loadDiagnostics(false))]),
  ]);
}

function liveCapabilityMatrix() {
  return [
    {
      area: "Dependency graph",
      demo: "n/a",
      live: state.selectedProjectId ? "Loaded from the selected project snapshot." : "Select or create a project to load graph data.",
      status: state.selectedProjectId ? "ready" : "needs project",
    },
    {
      area: "Task center",
      demo: "n/a",
      live: state.selectedProjectId ? "Task claim, release, heartbeat, sweep and context APIs are available." : "Requires a selected project.",
      status: state.selectedProjectId ? "ready" : "needs project",
    },
    {
      area: "Agents",
      demo: "n/a",
      live: "Agent roster is loaded from project state; external execution depends on CLI/LLM configuration.",
      status: "check diagnostics",
    },
    {
      area: "Review artifacts",
      demo: "n/a",
      live: state.selectedProjectId ? "Stored artifacts and human-control APIs are available." : "Requires a selected project.",
      status: state.selectedProjectId ? "ready" : "needs project",
    },
    {
      area: "Todo API",
      demo: "n/a",
      live: "Backed by platform Todo API endpoints and session storage.",
      status: "ready",
    },
    {
      area: "External execution",
      demo: "n/a",
      live: "Requires Agent CLI and/or LLM settings to pass diagnostics before real runs.",
      status: "requires setup",
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
      item.demo && item.demo !== "n/a" ? el("p", { text: `Demo: ${item.demo}` }) : null,
      el("p", { text: `Live: ${item.live}` }),
    ]))),
  ];
}

function renderTodos() {
  const todosPayload = state.demoMode ? ensureDemoTodos() : state.todos;
  const statsPayload = state.todoStats;
  return el("div", { class: "two-pane" }, [
    el("section", { class: "panel" }, [
      sectionTitle("Todo API", "Auxiliary CRUD coverage"),
      el("div", { class: "form-row" }, [input("Title", "todo-title", "Review graph"), input("Content", "todo-content", "Check API coverage")]),
      el("div", { class: "command-grid" }, [cmd("Create", createTodo), cmd("Refresh", loadTodos)]),
      table(["Title", "Status", "Content", "Actions"], asArray(todosPayload?.todos).map((todo) => [
        todo.title,
        todo.completed ? "done" : "open",
        short(todo.content, 80),
        el("div", { class: "row-actions" }, [cmd("View", () => loadTodo(todo.id)), cmd(todo.completed ? "Open" : "Done", () => updateTodo(todo.id, { completed: !todo.completed })), cmd("Delete", () => deleteTodo(todo.id))]),
      ])),
    ]),
    el("aside", { class: "panel" }, [
      sectionTitle("Todo Detail", `${todosPayload?.todos?.length || 0} loaded / ${statsPayload?.total ?? 0} total`),
      renderTodoStats(statsPayload),
      state.todoDetail ? el("pre", { class: "code tall", text: pretty(state.todoDetail) }) : null,
    ]),
  ]);
}

function renderTodoStats(stats) {
  return el("div", { class: "stats-grid" }, [
    metric("Total", stats?.total ?? 0),
    metric("Completed", stats?.completed ?? 0),
    metric("Active", stats?.active ?? 0),
  ]);
}

function settingsCard(title, key, load, save) {
  return card(title, [
    cmd("Load", async () => { await busy(async () => { state.settings[key] = await load(); }); }),
    cmd("Save", async () => {
      const parsed = parseJsonEditor(`settings-${key}`, state.settings[key] || {});
      await busy(async () => { state.settings[key] = await save(parsed); });
    }),
    state.settings[key] ? jsonEditor(`settings-${key}`, state.settings[key]) : el("p", { class: "muted", text: "Load settings to inspect payload." }),
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
    throw new Error(`Invalid JSON in ${id}: ${error.message}`);
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
      cmd("Create project", openCreateProject),
      state.demoMode ? null : cmd("Load Demo", enableDemoMode),
    ]),
  ]);
}
function logList(title, lines) {
  return el("div", { class: "log-group" }, [el("h3", { text: title }), ...lines.slice(-14).map((line) => el("div", { class: "log-line", text: short(line, 160) }))]);
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
    state.toast = "Create project is disabled in demo mode.";
    renderShell();
    return;
  }
  const requirement = prompt("Requirement", "Build a backend REST API for todo items with create, list, update, delete, and stats endpoints.");
  if (!requirement) return;
  const projectRoot = prompt("Project root", "");
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
    state.toast = `${action === "run" ? "Run" : "Step"} applied to demo project.`;
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
    state.toast = `${action.label} recorded.`;
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
      warnings: ["Demo mode does not call the backend."],
    };
    state.toast = "Demo mode keeps backend checks isolated.";
    renderShell();
    return;
  }
  state.loading = true;
  state.error = "";
  try {
    state.apiStatus = await api.request("/api/status");
    state.toast = "Backend status checked.";
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
async function claimTask(id) { if (demoAction(() => setTaskDetail(demoOperationResult(`Claim ${id}`)))) return; await busy(async () => { setTaskDetail(await api.claimTask(state.selectedProjectId, id, { agent_id: valueOf("task-agent") || "agent-backend", include_context: true, context_format: "markdown" })); await loadProject(state.selectedProjectId, true); }); }
async function claimNextTask() { if (demoAction(() => { setTaskDetail(demoOperationResult("Claim Next")); state.project = advanceDemoProject(state.project, "step"); })) return; await busy(async () => { setTaskDetail(await api.claimNext(state.selectedProjectId, { agent_id: valueOf("task-agent") || "agent-backend", include_context: true, context_format: "markdown" })); await loadProject(state.selectedProjectId, true); }); }
async function claimBatch() { if (demoAction(() => setTaskDetail({ ...demoOperationResult("Batch Claim"), claimed: snapshot().task_assignments?.slice(0, 2) || [] }))) return; await busy(async () => { setTaskDetail(await api.claimBatch(state.selectedProjectId, { agent_id: valueOf("task-agent") || "agent-backend", limit: 2 })); await loadProject(state.selectedProjectId, true); }); }
async function completeTask(id) { if (demoAction(() => { setTaskDetail(demoOperationResult(`Complete ${id}`)); state.project = advanceDemoProject(state.project, "step"); })) return; await busy(async () => { setTaskDetail(await api.completeTask(state.selectedProjectId, id, { agent_id: valueOf("task-agent") || undefined, claim_token: valueOf("task-token"), result_summary: valueOf("task-summary") })); await loadProject(state.selectedProjectId, true); }); }
async function failTask(id) { if (demoAction(() => setTaskDetail(demoOperationResult(`Fail ${id}`)))) return; await busy(async () => { setTaskDetail(await api.failTask(state.selectedProjectId, id, { agent_id: valueOf("task-agent") || undefined, claim_token: valueOf("task-token"), result_summary: valueOf("task-summary"), blocked_reason: valueOf("task-summary") || "Manual failure" })); await loadProject(state.selectedProjectId, true); }); }
async function heartbeatTask(id) { if (demoAction(() => setTaskDetail(demoOperationResult(`Heartbeat ${id}`)))) return; await busy(async () => { setTaskDetail(await api.heartbeatTask(state.selectedProjectId, id, { agent_id: valueOf("task-agent") || undefined, claim_token: valueOf("task-token") })); await loadProject(state.selectedProjectId, true); }); }
async function releaseTask(id) { if (demoAction(() => setTaskDetail(demoOperationResult(`Release ${id}`)))) return; await busy(async () => { setTaskDetail(await api.releaseTask(state.selectedProjectId, id, { agent_id: valueOf("task-agent") || undefined, claim_token: valueOf("task-token"), release_reason: "Released from console" })); await loadProject(state.selectedProjectId, true); }); }
async function releaseStale() { if (demoAction(() => setTaskDetail(demoOperationResult("Release Stale")))) return; await busy(async () => { setTaskDetail(await api.releaseStale(state.selectedProjectId, { stale_after_seconds: 3600, release_reason: "stale cleanup" })); await loadProject(state.selectedProjectId, true); }); }
async function releaseExpired() { if (demoAction(() => setTaskDetail(demoOperationResult("Release Expired")))) return; await busy(async () => { setTaskDetail(await api.releaseExpired(state.selectedProjectId, { release_reason: "expired lease cleanup" })); await loadProject(state.selectedProjectId, true); }); }
async function sweepTasks() { if (demoAction(() => setTaskDetail(demoOperationResult("Sweep")))) return; await busy(async () => { setTaskDetail(await api.sweep(state.selectedProjectId, { stale_after_seconds: 3600 })); await loadProject(state.selectedProjectId, true); }); }
async function claimAgentTask(agentId = valueOf("agent-id") || "agent-backend") { if (demoAction(() => { state.detail = demoOperationResult(`Agent ${agentId} claim`); })) return; await busy(async () => { state.detail = await api.claimAgentTask(state.selectedProjectId, agentId, { agent_id: agentId, include_context: true, context_format: "json" }); await loadProject(state.selectedProjectId, true); }); }
async function listAgentTasks() { if (demoAction(() => { state.detail = { agent_id: valueOf("agent-id") || "agent-backend", tasks: snapshot().task_assignments || [] }; })) return; await busy(async () => { state.detail = await api.agentTasks(state.selectedProjectId, valueOf("agent-id") || "agent-backend", true); }); }
async function loadArtifact(id) { if (demoAction(() => { state.detail = demoArtifact(id); })) return; await busy(async () => { state.detail = await api.artifact(state.selectedProjectId, id); }); }
async function loadHumanControl() { if (demoAction(() => { state.detail = snapshot().human_control || {}; })) return; await busy(async () => { state.detail = await api.humanControl(state.selectedProjectId); }); }
async function loadLiveState() { if (demoAction(() => { state.detail = state.project; })) return; await busy(async () => { state.detail = await api.live(state.selectedProjectId); }); }
async function humanAction(action) { if (demoAction(() => { state.detail = demoOperationResult(`Human ${action}`); })) return; await busy(async () => { state.detail = await api.humanAction(state.selectedProjectId, action, { actor: "operator", reason: `frontend ${action}` }); await loadProject(state.selectedProjectId, true); }); }
async function loadDiagnostics(probe) { if (demoAction(() => { state.diagnostics = demoDiagnostics(probe); })) return; await busy(async () => { state.diagnostics = await api.diagnostics({ probe_cli: probe, probe_llm: probe, preflight_llm: probe }); }); }
async function loadTodos() { if (demoAction(() => { resetDemoTodos(); state.todoDetail = null; })) return; await busy(async () => { state.todos = await api.todos(); state.todoStats = await api.todoStats(); }); }
async function createTodo() { const payload = { title: valueOf("todo-title"), content: valueOf("todo-content") }; if (demoAction(() => createDemoTodo(payload))) return; await busy(async () => { await api.createTodo(payload); state.todos = await api.todos(); state.todoStats = await api.todoStats(); }); }
async function loadTodo(id) { if (demoAction(() => { state.todoDetail = demoTodoDetailFromState(id); })) return; await busy(async () => { state.todoDetail = await api.getTodo(id); }); }
async function updateTodo(id, payload) { if (demoAction(() => updateDemoTodo(id, payload))) return; await busy(async () => { await api.updateTodo(id, payload); state.todos = await api.todos(); state.todoStats = await api.todoStats(); }); }
async function deleteTodo(id) { if (demoAction(() => deleteDemoTodo(id))) return; await busy(async () => { await api.deleteTodo(id); state.todos = await api.todos(); state.todoStats = await api.todoStats(); }); }

function demoAction(action) {
  if (!state.demoMode) return false;
  action();
  state.toast = "Demo state updated locally.";
  renderShell();
  return true;
}

function resetDemoMode() {
  loadDemoProject(0, true);
  state.context = null;
  state.detail = null;
  state.diagnostics = null;
  state.toast = "Demo mode reset.";
  renderShell();
}

function demoLlmPreflightAction() {
  state.diagnostics = demoLlmPreflight();
  state.toast = "Demo LLM preflight rendered locally.";
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
    title: payload.title?.trim() || "Untitled demo todo",
    content: payload.content?.trim() || "Created locally in Demo mode.",
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
    state.todoDetail = demoOperationResult(`Update missing todo ${id}`);
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
    state.todoDetail = demoOperationResult(`Delete missing todo ${id}`);
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
