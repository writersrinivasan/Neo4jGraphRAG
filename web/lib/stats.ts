import data from "@/data/analytics.json";

// Speedups are recomputed from the latest analytics run, so copy never drifts from the data.
export const speedups = data.benchmark.map((b) => b.sqlMs / b.graphMs);
export const minSpeed = Math.min(...speedups);
export const maxSpeed = Math.max(...speedups);
export const tokenRatio = (data.graphrag.sqlDumpTokens / data.graphrag.graphContextTokens).toFixed(1);
export const speedRange = `${minSpeed.toFixed(1)}-${maxSpeed.toFixed(1)}×`;
