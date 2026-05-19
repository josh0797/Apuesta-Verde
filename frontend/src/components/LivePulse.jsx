export function LivePulse({ minute, label = 'LIVE' }) {
  return (
    <span className="inline-flex items-center gap-2 px-2 py-0.5 rounded-md border border-emerald-500/30 bg-emerald-500/10 text-emerald-200 text-[11px] font-semibold tracking-wide" data-testid="live-pulse">
      <span className="live-pulse-dot" />
      {label}
      {minute !== undefined && minute !== null && (
        <span className="mono font-mono-tabular text-emerald-300">{minute}′</span>
      )}
    </span>
  );
}
