const clamp = (value, min, max) => Math.min(max, Math.max(min, value));

const variance = (values) => {
  if (!Array.isArray(values) || values.length === 0) return 0;
  const mean = values.reduce((sum, value) => sum + value, 0) / values.length;
  const squared = values.map((value) => (value - mean) ** 2);
  return squared.reduce((sum, value) => sum + value, 0) / squared.length;
};

export const estimateHashSkew = ({
  bucketLoads = [],
  replicas = 3,
  keyCardinality = 0,
}) => {
  const spread = Math.sqrt(variance(bucketLoads));
  const replicaFactor = clamp(replicas, 1, 9);
  const cardinalityFactor = clamp(Math.log2(Math.max(keyCardinality, 1)), 0, 20);

  const normalized = spread / Math.max(replicaFactor, 1) + cardinalityFactor * 0.4;
  const skewScore = Math.round(clamp(normalized * 6, 0, 100));

  return {
    skewScore,
    spread: Number(spread.toFixed(3)),
    recommendation: skewScore > 62 ? "rebalance" : "hold",
  };
};

export const computeShardPressure = ({ skewScore = 0, pendingMoves = 0 }) => {
  const pressure = clamp(skewScore * 0.7 + pendingMoves * 3.5, 0, 100);
  return Math.round(pressure);
};
