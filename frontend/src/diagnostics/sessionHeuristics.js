const clamp = (value, min, max) => Math.min(max, Math.max(min, value));

export const buildSessionHealthScore = ({
  reconnects = 0,
  avgLatencyMs = 0,
  retries = 0,
  queueDepth = 0,
}) => {
  const reconnectPenalty = clamp(reconnects * 3.2, 0, 28);
  const latencyPenalty = clamp(avgLatencyMs / 18, 0, 32);
  const retryPenalty = clamp(retries * 2.4, 0, 24);
  const queuePenalty = clamp(queueDepth * 1.1, 0, 16);

  const rawScore = 100 - reconnectPenalty - latencyPenalty - retryPenalty - queuePenalty;
  return Math.round(clamp(rawScore, 0, 100));
};

export const classifySessionHealth = (score) => {
  if (score >= 85) return "stable";
  if (score >= 65) return "watch";
  if (score >= 45) return "degraded";
  return "critical";
};

export const projectRebalanceWindow = (score, jitterMs = 0) => {
  const normalizedJitter = clamp(jitterMs, 0, 250);
  const baseline = score >= 70 ? 220 : 360;
  return baseline + normalizedJitter;
};
