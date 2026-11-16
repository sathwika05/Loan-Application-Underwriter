import { ApiError } from "../api";

interface Props {
  error: unknown;
  onRetry?: () => void;
}

export function ErrorBox({ error, onRetry }: Props) {
  const message = error instanceof Error ? error.message : "Something went wrong.";
  const requestId = error instanceof ApiError ? error.requestId : null;
  return (
    <div className="error" role="alert">
      <p>{message}</p>
      <p className="muted">Request ID: {requestId ?? "n/a"}</p>
      {onRetry && (
        <button type="button" onClick={onRetry}>
          Retry
        </button>
      )}
    </div>
  );
}
