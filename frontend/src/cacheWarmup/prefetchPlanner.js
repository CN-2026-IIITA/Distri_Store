const clamp = (value, min, max) => Math.min(max, Math.max(min, value));

const percentile = (values, p) => {
  if (!Array.isArray(values) || values.length === 0) return 0;
  const sorted = [...values].sort((a, b) => a - b);
  const index = clamp(Math.floor((sorted.length - 1) * p), 0, sorted.length - 1);
  return sorted[index];
};

export const buildPrefetchPlan = ({
  latencies = [],
  missRate = 0,
  hotKeys = [],
  concurrency = 4,
}) => {
  const p90Latency = percentile(latencies, 0.9);
  const normalizedMissRate = clamp(missRate, 0, 1);
  const budget = Math.round(clamp(12 + normalizedMissRate * 40 - p90Latency / 40, 4, 40));
  const laneCount = clamp(concurrency, 1, 8);

  const selectedKeys = hotKeys.slice(0, budget);
  const lanes = Array.from({ length: laneCount }, () => []);

  selectedKeys.forEach((key, index) => {
    lanes[index % laneCount].push(key);
  });

  return {
    budget,
    laneCount,
    p90Latency,
    lanes,
  };
};

export const estimateWarmupWindowMs = ({ budget = 0, laneCount = 1, p90Latency = 0 }) => {
  const effectiveLanes = clamp(laneCount, 1, 8);
  const batches = Math.ceil(clamp(budget, 0, 200) / effectiveLanes);
  return batches * clamp(Math.round(p90Latency), 10, 500);
};
