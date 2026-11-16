// Typed API client: the single place the front-end talks to the back-end.
// The base URL comes from VITE_API_BASE_URL; no secrets ever live here.

const BASE_URL: string = (import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000").replace(/\/$/, "");

export type LoanType = "mortgage" | "auto" | "personal" | "business";
export type Region = "East" | "West" | "Midwest" | "South";
export type Priority = "normal" | "high" | "urgent";
export type AppStatus = "NEW" | "ROUTED" | "QUEUED" | "COMPLETED";
export type Weights = Record<"expertise" | "capacity" | "sla" | "fairness", number>;

export const LOAN_TYPES: LoanType[] = ["mortgage", "auto", "personal", "business"];
export const REGIONS: Region[] = ["East", "West", "Midwest", "South"];
export const PRIORITIES: Priority[] = ["normal", "high", "urgent"];

export interface ApplicationInput {
  idempotency_key: string;
  applicant_name: string;
  submitter: string;
  loan_type: LoanType;
  amount: number;
  region: Region;
  credit_score: number;
  priority: Priority;
  notes: string | null;
}

export interface Application {
  id: number;
  applicant_name: string;
  submitter: string;
  loan_type: LoanType;
  amount: number;
  region: Region;
  credit_score: number | null;
  priority: Priority;
  notes: string | null;
  status: AppStatus;
  sla_due_at: string;
  created_at: string;
}

export interface ApplicationListItem extends Application {
  underwriter_name: string | null;
}

export interface Candidate {
  underwriter_id: number;
  name: string;
  load: number;
  max_caseload: number;
  sla_risk: "low" | "medium" | "high";
  components: Record<string, number>;
  rules_score: number;
  ai_delta?: number;
  ai_reason?: string;
  final_score?: number;
}

export interface Exclusion {
  underwriter_id: number;
  name: string;
  reasons: string[];
}

export interface Decision {
  candidates?: Candidate[];
  excluded?: Exclusion[];
  used_fallback?: boolean;
  fallback_reason?: string | null;
  model?: string;
  eligibility_warnings?: string[];
  weights_version?: number;
  weights?: Weights | null;
}

export interface OutcomeView {
  id: number;
  decision: "approved" | "declined";
  turnaround_hours: number;
  sla_met: boolean;
  rework: boolean;
  recorded_by: string;
  created_at: string;
}

export interface OutcomeStats {
  sample_size: number;
  sla_miss_rate: number;
  rework_rate: number;
  total_outcomes: number;
}

export interface LearningState {
  weights_version: number;
  weights: Weights;
  stats: OutcomeStats;
  history: { version: number; weights: Weights; reason: string; created_at: string }[];
}

export interface StatChange {
  quality_score: number;
  avg_turnaround_hours: number;
}

export interface CompleteResult {
  outcome: OutcomeView;
  learning: {
    underwriter: { name: string; before: StatChange; after: StatChange };
    weights_version: number;
    weights_changed: boolean;
    new_weights?: Weights;
    reason?: string;
    stats: OutcomeStats;
  };
  rerouted_queued: { application_id: number; run_id: string }[];
  request_id: string;
}

export interface AssignmentView {
  id: number;
  underwriter_id: number;
  underwriter_name: string;
  method: "AUTO" | "OVERRIDE";
  is_active: boolean;
  rationale: string;
  decision: Decision;
  override_reason: string | null;
  created_by: string;
  created_at: string;
}

export interface AuditEventView {
  id: number;
  event_type: string;
  actor: string;
  payload: Record<string, unknown>;
  created_at: string;
}

export interface NotificationView {
  recipient: string;
  message: string;
  created_at: string;
}

export interface ApplicationDetail {
  application: Application;
  active_assignment: AssignmentView | null;
  outcome: OutcomeView | null;
  history: AssignmentView[];
  audit: AuditEventView[];
  notifications: NotificationView[];
  latest_run_id: string | null;
}

export interface UnderwriterView {
  id: number;
  name: string;
  email: string;
  loan_types: LoanType[];
  regions: Region[];
  max_amount: number;
  load: number;
  max_caseload: number;
  quality_score: number;
  avg_turnaround_hours: number;
  completed: number;
  active: boolean;
}

export interface Dashboard {
  caseloads: UnderwriterView[];
  counts_by_status: Record<AppStatus, number>;
  learning: LearningState;
  recent_assignments: {
    application_id: number;
    applicant_name: string;
    underwriter_name: string;
    method: "AUTO" | "OVERRIDE";
    is_active: boolean;
    ended: "completed" | "reassigned" | null;
    created_at: string;
  }[];
}

export type StepStatus = "pending" | "running" | "done" | "skipped";
export type ToolStatus = "idle" | "running" | "done" | "error" | "limit_reached";

export interface Run {
  run_id: string;
  kind: "route" | "ask";
  application_id: number;
  request_id: string;
  status: "running" | "succeeded" | "failed";
  steps: { name: string; status: StepStatus; detail?: string }[];
  tools: Record<string, { calls: number; limit: number; status: ToolStatus }>;
  result: Record<string, unknown> | null;
  error: string | null;
}

export interface RunStarted {
  application_id: number;
  run_id: string | null;
  duplicate?: boolean;
  request_id: string;
}

export interface OverrideResult {
  assignment: AssignmentView;
  warnings: string[];
  request_id: string;
}

export class ApiError extends Error {
  readonly status: number;
  readonly requestId: string | null;

  constructor(message: string, status: number, requestId: string | null) {
    super(message);
    this.name = "ApiError";
    this.status = status;
    this.requestId = requestId;
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(`${BASE_URL}${path}`, {
      ...init,
      headers: { "Content-Type": "application/json", ...(init?.headers ?? {}) },
    });
  } catch {
    throw new ApiError(`Cannot reach the server at ${BASE_URL}.`, 0, null);
  }
  const requestId = res.headers.get("X-Request-ID");
  const body: unknown = await res.json().catch(() => null);
  if (!res.ok) {
    const b = (body ?? {}) as { detail?: unknown; request_id?: string };
    const detail = typeof b.detail === "string" ? b.detail : `Request failed (${res.status})`;
    throw new ApiError(detail, res.status, b.request_id ?? requestId);
  }
  return body as T;
}

export const api = {
  createApplication: (input: ApplicationInput) =>
    request<RunStarted>("/api/applications", { method: "POST", body: JSON.stringify(input) }),
  listApplications: (status?: AppStatus) =>
    request<ApplicationListItem[]>(`/api/applications${status ? `?status=${status}` : ""}`),
  getApplication: (id: number) => request<ApplicationDetail>(`/api/applications/${id}`),
  reroute: (id: number) => request<RunStarted>(`/api/applications/${id}/route`, { method: "POST" }),
  override: (id: number, underwriter_id: number, reason: string, actor: string) =>
    request<OverrideResult>(`/api/applications/${id}/override`, {
      method: "POST",
      body: JSON.stringify({ underwriter_id, reason, actor }),
    }),
  ask: (id: number, question: string) =>
    request<RunStarted>(`/api/applications/${id}/ask`, { method: "POST", body: JSON.stringify({ question }) }),
  complete: (id: number, decision: "approved" | "declined", turnaround_hours: number, rework: boolean, actor: string) =>
    request<CompleteResult>(`/api/applications/${id}/complete`, {
      method: "POST",
      body: JSON.stringify({ decision, turnaround_hours, rework, actor }),
    }),
  getRun: (runId: string) => request<Run>(`/api/runs/${runId}`),
  underwriters: () => request<UnderwriterView[]>("/api/underwriters"),
  dashboard: () => request<Dashboard>("/api/dashboard"),
};
