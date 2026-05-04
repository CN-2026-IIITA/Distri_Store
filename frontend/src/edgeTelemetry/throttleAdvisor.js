const clamp = (value, min, max) => Math.min(max, Math.max(min, value));

export const buildThrottleProfile = ({
  bandwidthKbps = 0,
  packetLoss = 0,
  peerCount = 0,
  retryBurst = 0,
}) => {
  const bandwidthScore = clamp(bandwidthKbps / 120, 0, 40);
  const lossPenalty = clamp(packetLoss * 180, 0, 30);
  const peerPenalty = clamp(peerCount * 1.4, 0, 18);
  const burstPenalty = clamp(retryBurst * 2.1, 0, 20);

  const headroom = Math.round(clamp(65 + bandwidthScore - lossPenalty - peerPenalty - burstPenalty, 8, 95));
  const throttleStep = Math.max(1, Math.round((100 - headroom) / 8));

  return {
    headroom,
    throttleStep,
    windowMs: 120 + throttleStep * 35,
  };
};

export const deriveBackoffSchedule = (throttleStep = 1, attempts = 4) => {
  const step = clamp(throttleStep, 1, 12);
  const count = clamp(attempts, 1, 8);
  const schedule = [];

  for (let i = 0; i < count; i += 1) {
    schedule.push((i + 1) * step * 45);
  }

  return schedule;
};
