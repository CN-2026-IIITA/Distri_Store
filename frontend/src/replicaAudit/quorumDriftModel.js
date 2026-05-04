const clamp = (value, min, max) => Math.min(max, Math.max(min, value));

export const buildQuorumDriftModel = ({
  ackLatencies = [],
  staleReplicaRatio = 0,
  transientTimeouts = 0,
  quorumSize = 3,
}) => {
  const avgLatency =
    ackLatencies.length > 0
      ? ackLatencies.reduce((sum, value) => sum + value, 0) / ackLatencies.length
      : 0;

  const latencyPenalty = clamp(avgLatency / 22, 0, 35);
  const stalePenalty = clamp(staleReplicaRatio * 55, 0, 40);
  const timeoutPenalty = clamp(transientTimeouts * 2.3, 0, 22);
  const quorumFactor = clamp(quorumSize, 1, 7);

  const driftScore = Math.round(
    clamp(latencyPenalty + stalePenalty + timeoutPenalty + (7 - quorumFactor) * 1.8, 0, 100),
  );

  return {
    driftScore,
    avgLatency: Math.round(avgLatency),
    mode: driftScore > 60 ? "unstable" : "stable",
  };
};

export const predictReconciliationRounds = (driftScore = 0, batchSize = 25) => {
  const normalizedBatch = clamp(batchSize, 1, 100);
  const rounds = Math.ceil((clamp(driftScore, 0, 100) / 10) * (30 / normalizedBatch));
  return clamp(rounds, 1, 12);
};
