import type { Weights } from "./api";

export function formatWeights(w: Weights): string {
  return (Object.entries(w) as [keyof Weights, number][]).map(([k, v]) => `${k} ${v.toFixed(3)}`).join(" · ");
}

export const pct = (x: number) => `${Math.round(x * 100)}%`;
