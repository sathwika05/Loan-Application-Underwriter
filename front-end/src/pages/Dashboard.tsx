import { api } from "../api";
import { ErrorBox } from "../components/ErrorBox";
import { formatWeights, pct } from "../format";
import { useLoad } from "../useLoad";

interface Props {
  onOpenApplication: (id: number) => void;
}

export function Dashboard({ onOpenApplication }: Props) {
  const { data, error, loading, reload, loadedAt } = useLoad(api.dashboard);

  return (
    <div className="page">
      <div className="row">
        <h1 className="grow">Dashboard</h1>
        {loadedAt && (
          <span className="muted" aria-live="polite">
            Updated {loadedAt.toLocaleTimeString()}
          </span>
        )}
        <button type="button" className="secondary" onClick={reload} disabled={loading}>
          {loading ? "Refreshing…" : "Refresh"}
        </button>
      </div>
      {loading && !data && <p>Loading…</p>}
      {error !== null && <ErrorBox error={error} onRetry={reload} />}
      {data && (
        <>
          <div className="tiles">
            {(["ROUTED", "QUEUED", "COMPLETED", "NEW"] as const).map((s) => (
              <div key={s} className={`tile ${s === "QUEUED" && data.counts_by_status[s] > 0 ? "alert" : ""}`}>
                <span className="tile-value">{data.counts_by_status[s]}</span>
                <span className="muted">{s}</span>
              </div>
            ))}
          </div>

          <section>
            <h2>Underwriter caseloads</h2>
            <table>
              <thead>
                <tr>
                  <th scope="col">Underwriter</th>
                  <th scope="col">Loan types</th>
                  <th scope="col">Regions</th>
                  <th scope="col">Caseload</th>
                  <th scope="col">Quality</th>
                  <th scope="col">Avg turnaround</th>
                  <th scope="col">Completed</th>
                  <th scope="col">Status</th>
                </tr>
              </thead>
              <tbody>
                {data.caseloads.map((u) => {
                  const pct = Math.round((u.load / u.max_caseload) * 100);
                  return (
                    <tr key={u.id}>
                      <td>{u.name}</td>
                      <td>{u.loan_types.join(", ")}</td>
                      <td>{u.regions.join(", ")}</td>
                      <td>
                        <div
                          className="bar"
                          role="meter"
                          aria-valuemin={0}
                          aria-valuemax={u.max_caseload}
                          aria-valuenow={u.load}
                          aria-label={`${u.name} caseload`}
                        >
                          <div className={`bar-fill ${pct >= 80 ? "hot" : ""}`} style={{ width: `${pct}%` }} />
                        </div>
                        {u.load}/{u.max_caseload}
                      </td>
                      <td>{u.quality_score.toFixed(2)}</td>
                      <td>{u.avg_turnaround_hours.toFixed(1)} h</td>
                      <td>{u.completed}</td>
                      <td>{u.active ? "Active" : "On leave"}</td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </section>

          <section className="card">
            <h2>Adaptive learning</h2>
            <p>
              Routing uses weights <strong>v{data.learning.weights_version}</strong>: {formatWeights(data.learning.weights)}
            </p>
            <p className="muted">
              Learned from {data.learning.stats.total_outcomes} outcome(s) · last {data.learning.stats.sample_size}: SLA
              miss rate {pct(data.learning.stats.sla_miss_rate)}, rework rate {pct(data.learning.stats.rework_rate)}.
              Quality and turnaround in the table above update after every recorded outcome.
            </p>
            {data.learning.history.length > 0 ? (
              <ul>
                {data.learning.history.map((h) => (
                  <li key={h.version}>
                    <strong>v{h.version}</strong> · {formatWeights(h.weights)} — {h.reason}
                  </li>
                ))}
              </ul>
            ) : (
              <p className="muted">No weight changes yet (needs at least 3 recorded outcomes).</p>
            )}
          </section>

          <section>
            <h2>Recent assignments</h2>
            {data.recent_assignments.length === 0 ? (
              <p className="muted">No assignments yet.</p>
            ) : (
              <ul>
                {data.recent_assignments.map((r, i) => (
                  <li key={i}>
                    <button type="button" className="link" onClick={() => onOpenApplication(r.application_id)}>
                      #{r.application_id} {r.applicant_name}
                    </button>{" "}
                    → {r.underwriter_name} <span className="badge">{r.method}</span>
                    {r.ended && <span className="muted"> ({r.ended})</span>}
                  </li>
                ))}
              </ul>
            )}
          </section>
        </>
      )}
    </div>
  );
}
