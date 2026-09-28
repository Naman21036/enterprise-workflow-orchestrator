export interface WorkflowRunRequest {
  goal: string;
  target_app?: string;
  input_parameters?: Record<string, any>;
  force_mode?: string;
}

export interface ExecutionResult {
  run_id: string;
  recording_id?: string;
  replay_run_id?: string;
  goal: string;
  execution_mode: string;
  capability_id?: string;
  capability_version?: string;
  status: 'SUCCESS' | 'BUSINESS_OUTCOME' | 'BLOCKED' | 'FAILED' | 'RUNNING' | 'CANCELLED';
  duration_seconds?: number;
  outputs?: Record<string, any>;
  step_logs?: any[];
  error?: string;
}

const API_BASE = '/api/v1';

async function readJson<T = any>(res: Response, fallback: string): Promise<T> {
  let body: any;
  try { body = await res.json(); } catch { body = null; }
  if (!res.ok) throw new Error(body?.detail?.message || body?.detail?.error || body?.detail || body?.message || fallback);
  return body as T;
}

export async function fetchHealth() {
  return readJson(await fetch(`${API_BASE}/health`), 'Health check failed');
}

export async function runWorkflowGoal(req: WorkflowRunRequest): Promise<ExecutionResult> {
  const res = await fetch(`${API_BASE}/workflows/run`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'Idempotency-Key': crypto.randomUUID() },
    body: JSON.stringify(req)
  });
  return readJson<ExecutionResult>(res, 'Workflow execution failed');
}

export async function triggerReplay(capability_id: string, parameters: Record<string, any>, version = '1.0.0'): Promise<ExecutionResult> {
  const res = await fetch(`${API_BASE}/workflows/replay`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'Idempotency-Key': crypto.randomUUID() },
    body: JSON.stringify({ capability_id, version, parameters })
  });
  return readJson<ExecutionResult>(res, 'Replay failed');
}

export async function fetchCapabilities() {
  return readJson(await fetch(`${API_BASE}/capabilities`), 'Capability registry unavailable');
}

export async function fetchCapabilityArtifact(capability_id: string) {
  const res = await fetch(`${API_BASE}/capabilities/${encodeURIComponent(capability_id)}`);
  return readJson(res, 'Capability artifact unavailable');
}

export async function fetchRuns() {
  return readJson(await fetch(`${API_BASE}/runs`), 'Workflow run history unavailable');
}

export async function fetchRunDetails(run_id: string) {
  const res = await fetch(`${API_BASE}/runs/${encodeURIComponent(run_id)}`);
  return readJson(res, 'Workflow run not found');
}

export async function fetchHandoffInfo(run_id: string) {
  const res = await fetch(`${API_BASE}/runs/${encodeURIComponent(run_id)}/handoff`);
  return readJson(res, 'Handoff information unavailable');
}

export async function submitOperatorAction(run_id: string, action_type: string, params: Record<string, any>) {
  const res = await fetch(`${API_BASE}/runs/${encodeURIComponent(run_id)}/handoff/action`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ action_type, params })
  });
  return readJson(res, 'Operator action failed');
}

export async function resumeWorkflowRun(run_id: string) {
  const res = await fetch(`${API_BASE}/runs/${encodeURIComponent(run_id)}/resume`, {
    method: 'POST'
  });
  return readJson(res, 'Workflow resume failed');
}

export async function fetchSafetyPolicy() {
  return readJson(await fetch(`${API_BASE}/safety/policy`), 'Safety policy unavailable');
}

export async function fetchRecordings() {
  return readJson(await fetch(`${API_BASE}/recordings`), 'Could not load recordings');
}

export async function fetchRecording(recording_id: string) {
  const res = await fetch(`${API_BASE}/recordings/${encodeURIComponent(recording_id)}`);
  return readJson(res, 'Could not load recording details');
}
