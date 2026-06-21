const DEMO_PROJECT_ID = "demo-api-delivery";

const workitems = [
  {
    id: "workitem-requirement",
    stage: "requirement",
    kind: "frozen_requirement_spec",
    kind_label: "Requirement Spec",
    status: "done",
    status_label: "Done",
    description: "Freeze API-only todo service scope and acceptance contract.",
  },
  {
    id: "workitem-design",
    stage: "design",
    kind: "api_design",
    kind_label: "API Design",
    status: "done",
    status_label: "Done",
    description: "Define REST resources, SQLite persistence, validation and stats endpoints.",
  },
  {
    id: "workitem-api",
    stage: "development",
    kind: "api_implementation",
    kind_label: "API Implementation",
    status: "done",
    status_label: "Done",
    description: "Generate FastAPI service, contract tests and manifest evidence.",
  },
  {
    id: "workitem-validation",
    stage: "testing",
    kind: "api_validation",
    kind_label: "API Validation",
    status: "running",
    status_label: "Running",
    description: "Run pytest contract validation and requirement coverage checks.",
  },
  {
    id: "workitem-delivery",
    stage: "delivery",
    kind: "delivery_manifest",
    kind_label: "Delivery Manifest",
    status: "pending",
    status_label: "Pending",
    description: "Write final report, manifest and replay audit bundle.",
  },
];

const taskAssignments = [
  {
    id: "assignment-api-validation",
    workitem_id: "workitem-validation",
    role: "tester",
    role_label: "Tester",
    status: "claimable",
    status_label: "Claimable",
    parallel_safe: true,
  },
  {
    id: "assignment-delivery-review",
    workitem_id: "workitem-delivery",
    role: "solution_designer",
    role_label: "Solution Designer",
    status: "queued",
    status_label: "Queued",
    parallel_safe: false,
  },
  {
    id: "assignment-api-rework",
    workitem_id: "workitem-api",
    role: "backend_engineer",
    role_label: "Backend Engineer",
    status: "done",
    status_label: "Done",
    parallel_safe: true,
  },
];

const artifacts = [
  {
    id: "artifact-frozen-requirement",
    workitem_id: "workitem-requirement",
    kind: "frozen_requirement_spec",
    title: "Frozen API Requirement",
    version: 1,
    source_backend: "llm_harness/local",
    content: "Goal: API-only todo service with SQLite persistence, CRUD, filters, delete and stats. Non-goals: browser UI and login.",
  },
  {
    id: "artifact-api-implementation",
    workitem_id: "workitem-api",
    kind: "api_implementation",
    title: "FastAPI SQLite Implementation",
    version: 1,
    source_backend: "api_sqlite_delivery",
    content: "Changed files: app.py, pytest.ini, tests/test_api_contract.py. Validation: 1 passed, SQLite row_count=2.",
  },
  {
    id: "artifact-validation",
    workitem_id: "workitem-validation",
    kind: "api_validation",
    title: "API Contract Validation",
    version: 1,
    source_backend: "cli/shell",
    content: "POST /api/items -> 201\nGET /api/items/stats -> 200\nRequirement Coverage: pass\nDelivery Readiness: ready / 92",
  },
];

const agents = [
  {
    agent_id: "agent-requirement",
    role: "requirement_designer",
    role_label: "Requirement",
    active: false,
    status_label: "Idle",
    mission: "Freeze business scope and downstream acceptance contract.",
  },
  {
    agent_id: "agent-backend",
    role: "backend_engineer",
    role_label: "Backend",
    active: true,
    status_label: "Ready",
    mission: "Own API implementation, persistence and contract-test repair.",
  },
  {
    agent_id: "agent-tester",
    role: "tester",
    role_label: "Tester",
    active: true,
    status_label: "Claimable",
    mission: "Validate observable API behavior and requirement coverage.",
  },
  {
    agent_id: "agent-reviewer",
    role: "solution_designer",
    role_label: "Reviewer",
    active: false,
    status_label: "Waiting",
    mission: "Review delivery readiness, risks and manifest audit evidence.",
  },
];

const activationNodes = [
  {
    agent_id: "dynamic-backend-contracts",
    role: "backend_engineer",
    role_label: "Backend Contracts",
    active: true,
    status_label: "Active",
    reason: "API contract and data persistence scopes were split for review.",
  },
  {
    agent_id: "dynamic-tester-edge",
    role: "tester",
    role_label: "Edge Tester",
    active: true,
    status_label: "Active",
    reason: "Validation and delete/filter behavior need edge-case coverage.",
  },
];

const demoTodoItems = [
  {
    id: 1,
    title: "Verify API contract",
    content: "Confirm POST /api/items and GET /api/items/stats evidence is visible.",
    completed: true,
  },
  {
    id: 2,
    title: "Review delivery manifest",
    content: "Check readiness score, artifact list, and replay verification notes.",
    completed: false,
  },
  {
    id: 3,
    title: "Prepare live fallback",
    content: "Keep Demo mode available if the live backend is not running.",
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
      fallback: "offline demo fixture",
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
      note: "Demo mode renders deterministic fixtures and does not call a model provider.",
    },
    source: "demo-fixture",
    updated_at: "2026-06-22T10:30:00+08:00",
  },
};

export function demoProjects() {
  return [
    {
      project_id: DEMO_PROJECT_ID,
      status: "in_progress",
      status_label: "Demo",
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
  return artifact ? { artifact, content: artifact.content } : { artifact_id: artifactId, content: "Demo artifact not found." };
}

export function demoTaskContext(assignmentId) {
  const assignment = taskAssignments.find((item) => item.id === assignmentId);
  return {
    assignment_id: assignmentId,
    workitem_id: assignment?.workitem_id || "",
    agent_prompt: "Execute the assigned API validation task, preserve frozen scope, and return concrete evidence.",
    required_inputs: ["frozen_requirement_spec", "api_implementation"],
    expected_outputs: ["validation evidence", "coverage trace", "delivery readiness signal"],
  };
}

export function demoOperationResult(label) {
  return {
    ok: true,
    mode: "demo",
    message: `${label} recorded in local demo state.`,
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

export function demoLlmPreflight() {
  return {
    ok: true,
    mode: "demo",
    provider: "offline",
    checks: [
      { name: "configuration", status: "pass", detail: "Offline fixture is available." },
      { name: "model_call", status: "skipped", detail: "Demo mode does not call an external LLM." },
      { name: "fallback", status: "pass", detail: "Deterministic delivery data is loaded locally." },
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
      live_api: "not required in demo mode",
      cli_probe: probe ? "skipped in offline demo" : "not requested",
      llm_preflight: probe ? "offline fixture pass" : "not requested",
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
    project_status_label: step >= 2 ? "Ready" : "In Progress",
    current_stage: step >= 2 ? "delivery" : step >= 1 ? "testing" : "development",
    run_audit: {
      risk_level: risk,
      risk_level_label: label(risk),
      delivery_readiness_status: step >= 2 ? "ready" : "at_risk",
      delivery_readiness_status_label: step >= 2 ? "Ready" : "At Risk",
      delivery_readiness_score: readiness,
    },
    task_center_summary: {
      queued: step >= 2 ? 0 : 1,
      claimable: step >= 1 ? 0 : 1,
      claimed: step >= 1 ? 1 : 0,
      done: step >= 2 ? 3 : 2,
    },
    blockers: step >= 1 ? [] : ["Validation scope is still running."],
    workitems: updatedWorkitems,
    task_assignments: taskAssignments.map((item) => {
      if (item.id === "assignment-api-validation" && step >= 1) return { ...item, status: "done", status_label: "Done" };
      if (item.id === "assignment-delivery-review" && step >= 2) return { ...item, status: "done", status_label: "Done" };
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
      guidance: step >= 2 ? "Delivery manifest is ready for review." : "Continue validation or inspect task context before delivery.",
      actions: [
        { id: "claim-next", label: "Claim Next", enabled: step < 2, category: "task_center", api_method: "POST", api_path: `/api/projects/${DEMO_PROJECT_ID}/tasks/claim-next` },
        { id: "sweep", label: "Sweep", enabled: true, category: "task_center", api_method: "POST", api_path: `/api/projects/${DEMO_PROJECT_ID}/tasks/sweep` },
        { id: "approve", label: "Approve", enabled: step >= 2, category: "human_control", api_method: "POST", api_path: `/api/projects/${DEMO_PROJECT_ID}/human-control/approve` },
      ],
    },
    recent_events_tail: [
      "Project demo-api-delivery created with API-only run profile.",
      "WorkItem workitem-api uses ApiSqliteDelivery for a verifiable SQLite API service.",
      "POST /api/items -> status_code=201 response payload={item}",
      step >= 1 ? "Requirement Coverage: pass; Delivery Readiness: at_risk / 92" : "Validation task is running.",
      step >= 2 ? "Manifest verification passed; delivery report written." : "Waiting for final delivery review.",
    ],
    route_lines: [
      "requirement -> design -> development -> testing -> delivery",
      "api_implementation -> api_validation -> delivery_manifest",
    ],
    executions: [
      { workitem_id: "workitem-api", agent_role: "backend_engineer", agent_label: "Backend", status: "success", status_label: "Success", source_backend: "api_sqlite_delivery" },
      { workitem_id: "workitem-validation", agent_role: "tester", agent_label: "Tester", status: validationStatus === "done" ? "success" : "running", status_label: label(validationStatus), source_backend: "cli/shell" },
      { workitem_id: "workitem-delivery", agent_role: "solution_designer", agent_label: "Reviewer", status: deliveryStatus, status_label: label(deliveryStatus), source_backend: "manifest" },
    ],
  };
}

function demoGoal() {
  return "Build an API-only todo service with FastAPI, SQLite persistence, CRUD, delete, filters, stats, contract tests and manifest audit evidence.";
}

function label(value) {
  return String(value || "").replace(/_/g, " ").replace(/\b\w/g, (char) => char.toUpperCase());
}

function clone(value) {
  return JSON.parse(JSON.stringify(value));
}
