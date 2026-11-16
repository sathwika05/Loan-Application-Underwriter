import { useCallback, useState, type FormEvent } from "react";
import { api, type AppStatus, type ApplicationDetail as Detail, type CompleteResult } from "../api";
import { formatWeights } from "../format";
import { ErrorBox } from "../components/ErrorBox";
import { RunProgress } from "../components/RunProgress";
import { useLoad } from "../useLoad";
import { useRunPoller } from "../useRunPoller";

const money = (n: number) => n.toLocaleString("en-US", { style: "currency", currency: "USD", maximumFractionDigits: 0 });
const when = (s: string) => new Date(s).toLocaleString();

interface Props {
  selectedId: number | null;
  onSelect: (id: number | null) => void;
}

export function Applications({ selectedId, onSelect }: Props) {
  return selectedId === null ? <ApplicationList onSelect={onSelect} /> : (
    <ApplicationDetailView id={selectedId} onBack={() => onSelect(null)} />
  );
}

function ApplicationList({ onSelect }: { onSelect: (id: number) => void }) {
  const [status, setStatus] = useState<AppStatus | "">("");
  const loader = useCallback(() => api.listApplications(status || undefined), [status]);
  const { data, error, loading, reload } = useLoad(loader);

  return (
    <div className="page">
      <h1>Applications</h1>
      <div className="row">
        <label>
          Status{" "}
          <select value={status} onChange={(e) => setStatus(e.target.value as AppStatus | "")}>
            <option value="">All</option>
            <option value="ROUTED">ROUTED</option>
            <option value="QUEUED">QUEUED</option>
            <option value="COMPLETED">COMPLETED</option>
            <option value="NEW">NEW</option>
          </select>
        </label>
        <button type="button" className="secondary" onClick={reload}>
          Refresh
        </button>
      </div>
      {loading && <p>Loading…</p>}
      {error !== null && <ErrorBox error={error} onRetry={reload} />}
      {data && data.length === 0 && <p className="muted">No applications yet. Create one from “New Application”.</p>}
      {data && data.length > 0 && (
        <table>
          <thead>
            <tr>
              <th scope="col">#</th>
              <th scope="col">Applicant</th>
              <th scope="col">Type</th>
              <th scope="col">Amount</th>
              <th scope="col">Region</th>
              <th scope="col">Status</th>
              <th scope="col">Underwriter</th>
            </tr>
          </thead>
          <tbody>
            {data.map((a) => (
              <tr key={a.id} className="clickable" onClick={() => onSelect(a.id)}>
                <td>
                  <button type="button" className="link" onClick={() => onSelect(a.id)}>
                    {a.id}
                  </button>
                </td>
                <td>{a.applicant_name}</td>
                <td>{a.loan_type}</td>
                <td>{money(a.amount)}</td>
                <td>{a.region}</td>
                <td>
                  <span className={`badge badge-${a.status}`}>{a.status}</span>
                </td>
                <td>{a.underwriter_name ?? "—"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  );
}

function ApplicationDetailView({ id, onBack }: { id: number; onBack: () => void }) {
  const loader = useCallback(() => api.getApplication(id), [id]);
  const { data, error, loading, reload } = useLoad(loader);

  return (
    <div className="page">
      <button type="button" className="link" onClick={onBack}>
        ← All applications
      </button>
      {loading && !data && <p>Loading…</p>}
      {error !== null && <ErrorBox error={error} onRetry={reload} />}
      {data && <DetailBody detail={data} onChanged={reload} />}
    </div>
  );
}

function DetailBody({ detail, onChanged }: { detail: Detail; onChanged: () => void }) {
  const { application: a, active_assignment: asg } = detail;
  const candidates = asg?.decision.candidates ?? [];
  const excluded = asg?.decision.excluded ?? [];

  return (
    <>
      <h1>
        Application #{a.id} · {a.applicant_name} <span className={`badge badge-${a.status}`}>{a.status}</span>
      </h1>
      <p className="muted">
        {a.loan_type} · {money(a.amount)} · {a.region} · credit {a.credit_score ?? "—"} · {a.priority} priority · SLA
        due {when(a.sla_due_at)} · submitted by {a.submitter}
      </p>
      {a.notes && <p>Notes: {a.notes}</p>}

      {(a.status === "NEW" || a.status === "QUEUED") && <Reroute id={a.id} onDone={onChanged} />}

      {asg && (
        <section className="card">
          <h2>
            {a.status === "COMPLETED" ? "Worked by" : "Assigned to"} {asg.underwriter_name}{" "}
            <span className="badge">{asg.method}</span>
          </h2>
          <p>{asg.rationale}</p>
          {asg.decision.weights_version !== undefined && (
            <p className="muted">Scored with learned weights v{asg.decision.weights_version}.</p>
          )}
          {asg.decision.used_fallback && (
            <p className="muted">GenAI not used: {asg.decision.fallback_reason ?? "fallback"} — rules decision.</p>
          )}
          {(asg.decision.eligibility_warnings?.length ?? 0) > 0 && (
            <p className="muted">Override warnings: {asg.decision.eligibility_warnings?.join("; ")}</p>
          )}
        </section>
      )}

      {candidates.length > 0 && (
        <section>
          <h2>Candidate scores (top {candidates.length})</h2>
          <table>
            <thead>
              <tr>
                <th scope="col">Underwriter</th>
                <th scope="col">Load</th>
                <th scope="col">SLA risk</th>
                <th scope="col">Expertise</th>
                <th scope="col">Capacity</th>
                <th scope="col">SLA</th>
                <th scope="col">Fairness</th>
                <th scope="col">Rules</th>
                <th scope="col">AI Δ</th>
                <th scope="col">Final</th>
              </tr>
            </thead>
            <tbody>
              {candidates.map((c) => (
                <tr key={c.underwriter_id} title={c.ai_reason || undefined}>
                  <td>{c.name}</td>
                  <td>
                    {c.load}/{c.max_caseload}
                  </td>
                  <td>{c.sla_risk}</td>
                  <td>{c.components.expertise?.toFixed(2)}</td>
                  <td>{c.components.capacity?.toFixed(2)}</td>
                  <td>{c.components.sla?.toFixed(2)}</td>
                  <td>{c.components.fairness?.toFixed(2)}</td>
                  <td>{c.rules_score.toFixed(3)}</td>
                  <td>{(c.ai_delta ?? 0).toFixed(2)}</td>
                  <td>
                    <strong>{(c.final_score ?? c.rules_score).toFixed(3)}</strong>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {candidates.some((c) => c.ai_reason) && (
            <ul className="muted">
              {candidates
                .filter((c) => c.ai_reason)
                .map((c) => (
                  <li key={c.underwriter_id}>
                    {c.name}: {c.ai_reason}
                  </li>
                ))}
            </ul>
          )}
        </section>
      )}

      {excluded.length > 0 && (
        <section>
          <h2>Excluded underwriters</h2>
          <ul>
            {excluded.map((e) => (
              <li key={e.underwriter_id}>
                <strong>{e.name}</strong>: {e.reasons.join("; ")}
              </li>
            ))}
          </ul>
        </section>
      )}

      {detail.outcome && (
        <section className="card">
          <h2>Outcome</h2>
          <p>
            <strong>{detail.outcome.decision}</strong> in {detail.outcome.turnaround_hours} h ·{" "}
            {detail.outcome.sla_met ? "SLA met" : "SLA missed"} · {detail.outcome.rework ? "rework needed" : "no rework"} ·
            recorded by {detail.outcome.recorded_by}
          </p>
        </section>
      )}

      <Ask id={a.id} />
      {a.status === "ROUTED" && <Complete id={a.id} onDone={onChanged} />}
      {a.status !== "COMPLETED" && (
        <Override id={a.id} currentUnderwriterId={asg?.underwriter_id ?? null} onDone={onChanged} />
      )}

      <section>
        <h2>Audit trail</h2>
        <ol className="audit">
          {detail.audit.map((e) => (
            <li key={e.id}>
              <strong>{e.event_type}</strong> by {e.actor} · {when(e.created_at)}
              {typeof e.payload.reason === "string" && <> · reason: “{e.payload.reason}”</>}
            </li>
          ))}
        </ol>
      </section>

      <section>
        <h2>Notifications</h2>
        {detail.notifications.length === 0 ? (
          <p className="muted">None.</p>
        ) : (
          <ul>
            {detail.notifications.map((n, i) => (
              <li key={i}>
                <strong>{n.recipient}</strong>: {n.message}
              </li>
            ))}
          </ul>
        )}
      </section>
    </>
  );
}

function Reroute({ id, onDone }: { id: number; onDone: () => void }) {
  const [runId, setRunId] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);
  const { state, retry } = useRunPoller(runId);

  async function go() {
    setError(null);
    try {
      const r = await api.reroute(id);
      setRunId(r.run_id);
    } catch (e) {
      setError(e);
    }
  }

  return (
    <section className="card">
      <p>This application is not routed yet.</p>
      <button type="button" onClick={go} disabled={state.phase === "polling"}>
        Re-route now
      </button>
      {error !== null && <ErrorBox error={error} />}
      <RunProgress state={state} onRetry={retry} />
      {state.phase === "done" && (
        <button type="button" className="secondary" onClick={onDone}>
          Refresh details
        </button>
      )}
    </section>
  );
}

function Ask({ id }: { id: number }) {
  const [question, setQuestion] = useState("");
  const [runId, setRunId] = useState<string | null>(null);
  const [error, setError] = useState<unknown>(null);
  const { state, retry } = useRunPoller(runId);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setError(null);
    setRunId(null);
    try {
      const r = await api.ask(id, question.trim());
      setRunId(r.run_id);
    } catch (err) {
      setError(err);
    }
  }

  const answer = state.phase === "done" && state.run.status === "succeeded" ? state.run.result?.answer : null;

  return (
    <section className="card">
      <h2>Ask about this decision</h2>
      <form onSubmit={submit} className="row">
        <label className="grow">
          <span className="sr-only">Question</span>
          <input
            required
            minLength={3}
            value={question}
            placeholder="Why this underwriter and not someone else?"
            onChange={(e) => setQuestion(e.target.value)}
          />
        </label>
        <button type="submit" disabled={state.phase === "polling"}>
          Ask
        </button>
      </form>
      {error !== null && <ErrorBox error={error} />}
      <RunProgress state={state} onRetry={retry} />
      {typeof answer === "string" && <p className="answer">{answer}</p>}
    </section>
  );
}

function Complete({ id, onDone }: { id: number; onDone: () => void }) {
  const [decision, setDecision] = useState<"approved" | "declined">("approved");
  const [hours, setHours] = useState("24");
  const [rework, setRework] = useState(false);
  const [actor, setActor] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [result, setResult] = useState<CompleteResult | null>(null);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      setResult(await api.complete(id, decision, Number(hours), rework, actor.trim()));
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  if (result) {
    const { underwriter: u, weights_changed, weights_version, new_weights, reason } = result.learning;
    return (
      <section className="card" role="status">
        <h2>Outcome recorded — the system learned from it</h2>
        <ul>
          <li>
            {u.name}: quality {u.before.quality_score} → <strong>{u.after.quality_score}</strong>, avg turnaround{" "}
            {u.before.avg_turnaround_hours} h → <strong>{u.after.avg_turnaround_hours} h</strong>
          </li>
          <li>
            {weights_changed && new_weights ? (
              <>
                Routing weights updated to <strong>v{weights_version}</strong>: {formatWeights(new_weights)} — {reason}
              </>
            ) : (
              <>Routing weights unchanged (v{weights_version}; need at least 3 outcomes or a meaningful shift).</>
            )}
          </li>
          <li>
            {result.rerouted_queued.length} queued application(s) re-routed now that capacity is free.
          </li>
        </ul>
        <button type="button" className="secondary" onClick={onDone}>
          Refresh details
        </button>
      </section>
    );
  }

  return (
    <section className="card">
      <h2>Record outcome (feeds adaptive learning)</h2>
      <form onSubmit={submit} className="form">
        <label>
          Decision
          <select value={decision} onChange={(e) => setDecision(e.target.value as "approved" | "declined")}>
            <option value="approved">approved</option>
            <option value="declined">declined</option>
          </select>
        </label>
        <label>
          Turnaround (hours)
          <input required type="number" min={0.1} step="any" value={hours} onChange={(e) => setHours(e.target.value)} />
        </label>
        <label>
          Your name
          <input required value={actor} onChange={(e) => setActor(e.target.value)} />
        </label>
        <label className="check">
          <input type="checkbox" checked={rework} onChange={(e) => setRework(e.target.checked)} /> Rework was needed
        </label>
        <div className="wide">
          <button type="submit" disabled={busy}>
            {busy ? "Saving…" : "Complete case"}
          </button>
        </div>
      </form>
      {error !== null && <ErrorBox error={error} />}
    </section>
  );
}

function Override({
  id,
  currentUnderwriterId,
  onDone,
}: {
  id: number;
  currentUnderwriterId: number | null;
  onDone: () => void;
}) {
  const { data: uws, error: loadError } = useLoad(api.underwriters);
  const [uwId, setUwId] = useState("");
  const [reason, setReason] = useState("");
  const [actor, setActor] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const [message, setMessage] = useState<string | null>(null);

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      const r = await api.override(id, Number(uwId), reason.trim(), actor.trim());
      setMessage(
        `Reassigned to ${r.assignment.underwriter_name}.` +
          (r.warnings.length ? ` Warnings: ${r.warnings.join("; ")}` : ""),
      );
      setReason("");
      onDone();
    } catch (err) {
      setError(err);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="card">
      <h2>Override assignment</h2>
      {loadError !== null && <ErrorBox error={loadError} />}
      <form onSubmit={submit} className="form">
        <label>
          New underwriter
          <select required value={uwId} onChange={(e) => setUwId(e.target.value)}>
            <option value="">Select…</option>
            {uws
              ?.filter((u) => u.active && u.id !== currentUnderwriterId)
              .map((u) => (
                <option key={u.id} value={u.id}>
                  {u.name} ({u.load}/{u.max_caseload})
                </option>
              ))}
          </select>
        </label>
        <label>
          Your name
          <input required value={actor} onChange={(e) => setActor(e.target.value)} />
        </label>
        <label className="wide">
          Reason (required, min 10 characters)
          <textarea required minLength={10} rows={2} value={reason} onChange={(e) => setReason(e.target.value)} />
        </label>
        <div className="wide">
          <button type="submit" disabled={busy}>
            {busy ? "Saving…" : "Override"}
          </button>
        </div>
      </form>
      {error !== null && <ErrorBox error={error} />}
      {message && <p role="status">{message}</p>}
    </section>
  );
}
