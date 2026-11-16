import { useState, type FormEvent } from "react";
import {
  api,
  LOAN_TYPES,
  PRIORITIES,
  REGIONS,
  type ApplicationInput,
  type LoanType,
  type Priority,
  type Region,
} from "../api";
import { ErrorBox } from "../components/ErrorBox";
import { RunProgress } from "../components/RunProgress";
import { useRunPoller } from "../useRunPoller";

interface Props {
  onOpenApplication: (id: number) => void;
}

const EMPTY = {
  applicant_name: "",
  submitter: "",
  loan_type: "mortgage" as LoanType,
  amount: "450000",
  region: "East" as Region,
  credit_score: "720",
  priority: "normal" as Priority,
  notes: "",
};

export function NewApplication({ onOpenApplication }: Props) {
  const [form, setForm] = useState(EMPTY);
  // One key per form fill: double-submits return the same application.
  const [idemKey, setIdemKey] = useState(() => crypto.randomUUID());
  const [submitting, setSubmitting] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [runId, setRunId] = useState<string | null>(null);
  const [appId, setAppId] = useState<number | null>(null);
  const { state, retry } = useRunPoller(runId);

  const set = <K extends keyof typeof EMPTY>(k: K, v: (typeof EMPTY)[K]) => setForm((f) => ({ ...f, [k]: v }));

  async function submit(e: FormEvent) {
    e.preventDefault();
    setSubmitting(true);
    setError(null);
    const input: ApplicationInput = {
      idempotency_key: idemKey,
      applicant_name: form.applicant_name.trim(),
      submitter: form.submitter.trim(),
      loan_type: form.loan_type,
      amount: Number(form.amount),
      region: form.region,
      credit_score: Number(form.credit_score),
      priority: form.priority,
      notes: form.notes.trim() || null,
    };
    try {
      const started = await api.createApplication(input);
      setAppId(started.application_id);
      if (started.run_id) setRunId(started.run_id);
      else onOpenApplication(started.application_id);
    } catch (err) {
      setError(err);
    } finally {
      setSubmitting(false);
    }
  }

  function reset() {
    setForm(EMPTY);
    setIdemKey(crypto.randomUUID());
    setRunId(null);
    setAppId(null);
    setError(null);
  }

  const result = state.phase === "done" ? state.run.result : null;

  return (
    <div className="page">
      <h1>New loan application</h1>
      {!runId && (
        <form onSubmit={submit} className="form">
          <label>
            Applicant name
            <input required value={form.applicant_name} onChange={(e) => set("applicant_name", e.target.value)} />
          </label>
          <label>
            Submitter (you)
            <input required value={form.submitter} onChange={(e) => set("submitter", e.target.value)} />
          </label>
          <label>
            Loan type
            <select value={form.loan_type} onChange={(e) => set("loan_type", e.target.value as LoanType)}>
              {LOAN_TYPES.map((t) => (
                <option key={t}>{t}</option>
              ))}
            </select>
          </label>
          <label>
            Amount (USD)
            <input
              required
              type="number"
              min={1}
              step="any"
              value={form.amount}
              onChange={(e) => set("amount", e.target.value)}
            />
          </label>
          <label>
            Region
            <select value={form.region} onChange={(e) => set("region", e.target.value as Region)}>
              {REGIONS.map((r) => (
                <option key={r}>{r}</option>
              ))}
            </select>
          </label>
          <label>
            Credit score
            <input
              required
              type="number"
              min={300}
              max={850}
              value={form.credit_score}
              onChange={(e) => set("credit_score", e.target.value)}
            />
          </label>
          <label>
            Priority
            <select value={form.priority} onChange={(e) => set("priority", e.target.value as Priority)}>
              {PRIORITIES.map((p) => (
                <option key={p}>{p}</option>
              ))}
            </select>
          </label>
          <label className="wide">
            Notes
            <textarea
              rows={3}
              value={form.notes}
              placeholder="e.g. self-employed borrower, jumbo loan, two years of tax returns"
              onChange={(e) => set("notes", e.target.value)}
            />
          </label>
          <div className="wide">
            <button type="submit" disabled={submitting}>
              {submitting ? "Submitting…" : "Submit and route"}
            </button>
          </div>
        </form>
      )}
      {error !== null && <ErrorBox error={error} />}

      {runId && (
        <>
          <h2>Routing application #{appId}</h2>
          <RunProgress state={state} onRetry={retry} />
          {result && (
            <div className="card">
              {result.outcome === "ROUTED" && (
                <>
                  <h3>Assigned to {String(result.underwriter_name)}</h3>
                  <p>{String(result.rationale)}</p>
                </>
              )}
              {result.outcome === "QUEUED" && <h3>Queued: no eligible underwriter right now.</h3>}
              {result.outcome === "ALREADY_ROUTED" && <h3>Already routed (duplicate prevented).</h3>}
            </div>
          )}
          <div className="row">
            {appId !== null && (
              <button type="button" onClick={() => onOpenApplication(appId)}>
                View details
              </button>
            )}
            <button type="button" className="secondary" onClick={reset}>
              New application
            </button>
          </div>
        </>
      )}
    </div>
  );
}
