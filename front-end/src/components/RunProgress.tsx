import type { Run } from "../api";
import { POLL_ATTEMPTS, type PollState } from "../useRunPoller";
import { ErrorBox } from "./ErrorBox";

const STEP_ICON: Record<string, string> = { pending: "○", running: "◐", done: "●", skipped: "–" };

function RunDetails({ run }: { run: Run }) {
  const tools = Object.entries(run.tools);
  return (
    <div className="run">
      <ol className="steps" aria-label="Agent steps">
        {run.steps.map((s) => (
          <li key={s.name} className={`step step-${s.status}`}>
            <span aria-hidden="true">{STEP_ICON[s.status] ?? "○"}</span> {s.name.replace(/_/g, " ")}
            <span className="muted"> · {s.status}</span>
          </li>
        ))}
      </ol>
      {tools.length > 0 && (
        <table className="tools">
          <caption>Tool calls</caption>
          <thead>
            <tr>
              <th scope="col">Tool</th>
              <th scope="col">Status</th>
              <th scope="col">Calls / limit</th>
            </tr>
          </thead>
          <tbody>
            {tools.map(([name, t]) => (
              <tr key={name}>
                <td>
                  <code>{name}</code>
                </td>
                <td className={`tool-${t.status}`}>{t.status.replace("_", " ")}</td>
                <td>
                  {t.calls} / {t.limit}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
      <p className="muted">Request ID: {run.request_id}</p>
    </div>
  );
}

interface Props {
  state: PollState;
  onRetry: () => void;
}

export function RunProgress({ state, onRetry }: Props) {
  switch (state.phase) {
    case "idle":
      return null;
    case "polling":
      return (
        <section aria-live="polite">
          <p>
            Running… (check {state.attempt}/{POLL_ATTEMPTS})
          </p>
          {state.run && <RunDetails run={state.run} />}
        </section>
      );
    case "timeout":
      return (
        <section aria-live="polite">
          <div className="error" role="alert">
            <p>This is taking longer than expected. The run may still finish in the background.</p>
            <p className="muted">Request ID: {state.run?.request_id ?? "n/a"}</p>
            <button type="button" onClick={onRetry}>
              Retry
            </button>
          </div>
          {state.run && <RunDetails run={state.run} />}
        </section>
      );
    case "error":
      return <ErrorBox error={state.error} onRetry={onRetry} />;
    case "done":
      return (
        <section aria-live="polite">
          {state.run.status === "failed" && (
            <div className="error" role="alert">
              <p>The run failed: {state.run.error ?? "unknown error"}</p>
              <p className="muted">Request ID: {state.run.request_id}</p>
            </div>
          )}
          <RunDetails run={state.run} />
        </section>
      );
  }
}
