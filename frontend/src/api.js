const DEFAULT_API_BASE = "http://127.0.0.1:8000";

export function getApiBase() {
  const params = new URLSearchParams(window.location.search);
  return normalizeBase(params.get("api") || localStorage.getItem("conductor.apiBase") || DEFAULT_API_BASE);
}

export function setApiBase(value) {
  const normalized = normalizeBase(value || DEFAULT_API_BASE);
  localStorage.setItem("conductor.apiBase", normalized);
  return normalized;
}

function normalizeBase(value) {
  return String(value || DEFAULT_API_BASE).replace(/\/+$/, "");
}

function query(params = {}) {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value !== undefined && value !== null && value !== "") search.set(key, String(value));
  }
  const text = search.toString();
  return text ? `?${text}` : "";
}

export class ApiClient {
  constructor(baseUrl = getApiBase()) {
    this.baseUrl = normalizeBase(baseUrl);
  }

  setBaseUrl(value) {
    this.baseUrl = setApiBase(value);
  }

  async request(path, options = {}) {
    const headers = new Headers(options.headers || {});
    const hasBody = options.body !== undefined && options.body !== null;
    if (hasBody && !headers.has("Content-Type")) headers.set("Content-Type", "application/json");
    const response = await fetch(`${this.baseUrl}${path}`, {
      ...options,
      credentials: "include",
      headers,
      body: hasBody && typeof options.body !== "string" ? JSON.stringify(options.body) : options.body,
    });
    const type = response.headers.get("Content-Type") || "";
    const payload = type.includes("application/json") ? await response.json() : await response.text();
    if (!response.ok) {
      const message = typeof payload === "object" ? payload.detail || payload.error || JSON.stringify(payload) : payload;
      throw new Error(message || `HTTP ${response.status}`);
    }
    return payload;
  }

  listProjects() { return this.request("/api/projects"); }
  createProject(payload) { return this.request("/api/projects", { method: "POST", body: payload }); }
  project(projectId) { return this.request(`/api/projects/${encodeURIComponent(projectId)}`); }
  live(projectId) { return this.request(`/api/projects/${encodeURIComponent(projectId)}/live`); }
  artifact(projectId, artifactId) { return this.request(`/api/projects/${encodeURIComponent(projectId)}/artifacts/${encodeURIComponent(artifactId)}`); }
  humanControl(projectId) { return this.request(`/api/projects/${encodeURIComponent(projectId)}/human-control`); }
  humanAction(projectId, action, payload) { return this.request(`/api/projects/${encodeURIComponent(projectId)}/human-control/${action}`, { method: "POST", body: payload }); }
  tasks(projectId, params) { return this.request(`/api/projects/${encodeURIComponent(projectId)}/tasks${query(params)}`); }
  taskSummary(projectId, params) { return this.request(`/api/projects/${encodeURIComponent(projectId)}/tasks/summary${query(params)}`); }
  taskAgents(projectId, assignmentId) { return this.request(`/api/projects/${encodeURIComponent(projectId)}/tasks/${encodeURIComponent(assignmentId)}/agents`); }
  agentTasks(projectId, agentId, claimableOnly = false) { return this.request(`/api/projects/${encodeURIComponent(projectId)}/agents/${encodeURIComponent(agentId)}/tasks${query({ claimable_only: claimableOnly })}`); }
  claimAgentTask(projectId, agentId, payload) { return this.request(`/api/projects/${encodeURIComponent(projectId)}/agents/${encodeURIComponent(agentId)}/claim-task`, { method: "POST", body: payload }); }
  claimTask(projectId, assignmentId, payload) { return this.request(`/api/projects/${encodeURIComponent(projectId)}/tasks/${encodeURIComponent(assignmentId)}/claim`, { method: "POST", body: payload }); }
  claimNext(projectId, payload) { return this.request(`/api/projects/${encodeURIComponent(projectId)}/tasks/claim-next`, { method: "POST", body: payload }); }
  claimBatch(projectId, payload) { return this.request(`/api/projects/${encodeURIComponent(projectId)}/tasks/claim-batch`, { method: "POST", body: payload }); }
  taskContext(projectId, assignmentId, params) { return this.request(`/api/projects/${encodeURIComponent(projectId)}/tasks/${encodeURIComponent(assignmentId)}/context${query(params)}`); }
  completeTask(projectId, assignmentId, payload) { return this.request(`/api/projects/${encodeURIComponent(projectId)}/tasks/${encodeURIComponent(assignmentId)}/complete`, { method: "POST", body: payload }); }
  failTask(projectId, assignmentId, payload) { return this.request(`/api/projects/${encodeURIComponent(projectId)}/tasks/${encodeURIComponent(assignmentId)}/fail`, { method: "POST", body: payload }); }
  heartbeatTask(projectId, assignmentId, payload) { return this.request(`/api/projects/${encodeURIComponent(projectId)}/tasks/${encodeURIComponent(assignmentId)}/heartbeat`, { method: "POST", body: payload }); }
  releaseTask(projectId, assignmentId, payload) { return this.request(`/api/projects/${encodeURIComponent(projectId)}/tasks/${encodeURIComponent(assignmentId)}/release`, { method: "POST", body: payload }); }
  releaseStale(projectId, payload) { return this.request(`/api/projects/${encodeURIComponent(projectId)}/tasks/release-stale`, { method: "POST", body: payload }); }
  releaseExpired(projectId, payload) { return this.request(`/api/projects/${encodeURIComponent(projectId)}/tasks/release-expired-leases`, { method: "POST", body: payload }); }
  sweep(projectId, payload) { return this.request(`/api/projects/${encodeURIComponent(projectId)}/tasks/sweep`, { method: "POST", body: payload }); }
  step(projectId) { return this.request(`/api/projects/${encodeURIComponent(projectId)}/step`, { method: "POST" }); }
  run(projectId) { return this.request(`/api/projects/${encodeURIComponent(projectId)}/run`, { method: "POST" }); }
  executionSettings() { return this.request("/api/settings/execution"); }
  saveExecutionSettings(payload) { return this.request("/api/settings/execution", { method: "POST", body: payload }); }
  cliSettings() { return this.request("/api/settings/cli"); }
  saveCliSettings(payload) { return this.request("/api/settings/cli", { method: "POST", body: payload }); }
  llmSettings() { return this.request("/api/settings/llm"); }
  saveLlmSettings(payload) { return this.request("/api/settings/llm", { method: "POST", body: payload }); }
  llmPreflight(payload) { return this.request("/api/settings/llm/preflight", { method: "POST", body: payload }); }
  diagnostics(params) { return this.request(`/api/diagnostics${query(params)}`); }
  todos(params) { return this.request(`/api/todos${query(params)}`); }
  todoStats() { return this.request("/api/todos/stats"); }
  getTodo(todoId) { return this.request(`/api/todos/${encodeURIComponent(todoId)}`); }
  createTodo(payload) { return this.request("/api/todos", { method: "POST", body: payload }); }
  updateTodo(todoId, payload) { return this.request(`/api/todos/${encodeURIComponent(todoId)}`, { method: "PATCH", body: payload }); }
  deleteTodo(todoId) { return this.request(`/api/todos/${encodeURIComponent(todoId)}`, { method: "DELETE" }); }
}
