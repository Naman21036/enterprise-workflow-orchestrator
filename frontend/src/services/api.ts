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

export interface OperatorSession {
  operator_id: string;
  username: string;
  role: 'ADMIN' | 'OPERATOR' | 'VIEWER';
  tenant_id: string;
}

export async function fetchCurrentOperator(): Promise<OperatorSession> {
  return readJson(await fetch(`${API_BASE}/auth/me`, { credentials: 'same-origin' }), 'Sign in to continue');
}

export interface RegistrationPayload {
  full_name: string;
  username: string;
  email: string;
  password: string;
  confirm_password: string;
}

export async function registerOperator(payload: RegistrationPayload): Promise<void> {
  let res: Response;
  try {
    res = await fetch(`${API_BASE}/auth/register`, {
      method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
  } catch {
    throw new Error('Registration service is unavailable. Check that the API is initialized, then try again.');
  }
  await readJson(res, 'Registration could not be completed');
}

export async function loginOperator(username: string, password: string): Promise<OperatorSession> {
  let res: Response;
  try {
    res = await fetch(`${API_BASE}/auth/login`, {
      method: 'POST', credentials: 'same-origin', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password }),
    });
  } catch {
    throw new Error('Authentication service is unavailable. Check that the API is initialized, then try again.');
  }
  await readJson(res, 'Sign in failed');
  return fetchCurrentOperator();
}

export async function logoutOperator(): Promise<void> {
  await fetch(`${API_BASE}/auth/logout`, { method: 'POST', credentials: 'same-origin' });
}

async function readJson<T = any>(res: Response, fallback: string): Promise<T> {
  let body: any;
  try { body = await res.json(); } catch { body = null; }
  if (!res.ok && res.status >= 500 && body === null) {
    throw new Error('Authentication service is unavailable. Check backend setup and database initialization.');
  }
  if (!res.ok) {
    const detail = Array.isArray(body?.detail)
      ? body.detail.map((item: any) => item?.msg).filter(Boolean).join(' ')
      : body?.detail?.message || body?.detail?.error || body?.detail || body?.message;
    throw new Error(typeof detail === 'string' ? detail : fallback);
  }
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
  const action_id = crypto.randomUUID();
  const res = await fetch(`${API_BASE}/runs/${encodeURIComponent(run_id)}/handoff/action`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', 'Idempotency-Key': action_id },
    body: JSON.stringify({ action_type, action_id, params })
  });
  return readJson(res, 'Operator action failed');
}

export async function resumeWorkflowRun(run_id: string, inputs: Record<string, any> = {}, session_version?: number) {
  const res = await fetch(`${API_BASE}/runs/${encodeURIComponent(run_id)}/resume`, {
    method: 'POST', headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ inputs, session_version })
  });
  return readJson(res, 'Workflow resume failed');
}

export async function cancelWorkflowRun(run_id: string) {
  const res = await fetch(`${API_BASE}/runs/${encodeURIComponent(run_id)}/cancel`, { method: 'POST' });
  return readJson(res, 'Run cancellation failed');
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
