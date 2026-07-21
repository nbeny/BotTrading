/** Formatting helpers for a financial UI (currency, %, compact, relative time). */

const usdFmt = new Intl.NumberFormat('en-US', {
  style: 'currency',
  currency: 'USD',
  minimumFractionDigits: 2,
  maximumFractionDigits: 2,
});

const usdCompactFmt = new Intl.NumberFormat('en-US', {
  style: 'currency',
  currency: 'USD',
  notation: 'compact',
  maximumFractionDigits: 2,
});

export function fmtUsd(v: number | string | null | undefined): string {
  const n = typeof v === 'string' ? Number(v) : v;
  if (n == null || Number.isNaN(n)) return '—';
  return usdFmt.format(n);
}

export function fmtUsdCompact(v: number | string | null | undefined): string {
  const n = typeof v === 'string' ? Number(v) : v;
  if (n == null || Number.isNaN(n)) return '—';
  return usdCompactFmt.format(n);
}

export function fmtPct(v: number | null | undefined, digits = 2): string {
  if (v == null || Number.isNaN(v)) return '—';
  const s = v.toFixed(digits);
  return `${v > 0 ? '+' : ''}${s}%`;
}

export function fmtNum(v: number | null | undefined, digits = 2): string {
  if (v == null || Number.isNaN(v)) return '—';
  return v.toLocaleString('en-US', { maximumFractionDigits: digits });
}

export function fmtScore(v: number | null | undefined): string {
  if (v == null || Number.isNaN(v)) return '—';
  return String(Math.round(v));
}

export function fmtTime(iso: string | number | null | undefined): string {
  if (iso == null) return '—';
  const d = typeof iso === 'number' ? new Date(iso) : new Date(iso);
  if (Number.isNaN(d.getTime())) return '—';
  return d.toLocaleTimeString('fr-FR', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

export function fmtDateTime(iso: string | number | null | undefined): string {
  if (iso == null) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return '—';
  return d.toLocaleString('fr-FR', {
    day: '2-digit',
    month: '2-digit',
    hour: '2-digit',
    minute: '2-digit',
  });
}

/** Relative "il y a Xs / Xm / Xh" from an ISO/epoch timestamp given a `now` ref. */
export function fmtRelative(iso: string | number | null | undefined, now: number): string {
  if (iso == null) return '—';
  const t = typeof iso === 'number' ? iso : new Date(iso).getTime();
  if (Number.isNaN(t)) return '—';
  const s = Math.max(0, Math.round((now - t) / 1000));
  if (s < 60) return `il y a ${s}s`;
  const m = Math.round(s / 60);
  if (m < 60) return `il y a ${m}m`;
  const h = Math.round(m / 60);
  if (h < 24) return `il y a ${h}h`;
  return `il y a ${Math.round(h / 24)}j`;
}

export function scoreColor(score: number): 'success' | 'warning' | 'error' | 'default' {
  if (score >= 75) return 'success';
  if (score >= 50) return 'warning';
  if (score > 0) return 'error';
  return 'default';
}
