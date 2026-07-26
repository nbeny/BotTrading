'use client';

/**
 * How the "Balance Kraken" tile reads, in one place.
 *
 * Two tiles show this number — the dashboard KPI row and the Capital page KPI
 * row — and they must not diverge: a balance that says "non connecté" on one
 * page and `0 $` on the other is worse than either alone. The two live-feed
 * components in this repo already drifted apart that way and had to be merged.
 *
 * Deliberately a helper, not a card: the two tiles differ in icon size and in
 * what else goes in their footnote, so only the *reading of the number* is
 * shared, not the layout.
 */

import { Box } from '@mui/material';
import { fmtRelative, fmtUsd } from '@/lib/format';
import type { BalanceSource, Portfolio } from '@/lib/types/domain';

export interface KrakenBalanceView {
  /** Goes into `StatCard.value` — already styled for its state. */
  value: React.ReactNode;
  /** Goes into `StatCard.footnote`, alone or appended to the tile's own note. */
  note: React.ReactNode | undefined;
}

const VENUE_LABEL: Record<BalanceSource, string> = {
  kraken_spot: 'Kraken Spot',
  kraken_futures: 'Kraken Futures',
  unavailable: 'Kraken',
};

/**
 * Reading of a `Portfolio` balance, in three states:
 *
 * - **non connecté** — no snapshot (`null` amount, or `unavailable` source).
 *   Renders a dash *plus the words*, because a bare `—` is exactly what the
 *   tile shows while data is still arriving; the operator has to be able to
 *   tell "loading" from "no Kraken account wired up".
 * - **périmé** — an amount older than the freshness window. Shown greyed with
 *   its age: a stale number beats a blank, but only if it announces itself.
 * - **frais** — the amount, plus which Kraken wallet it came from.
 *
 * The age always comes from `balance_fetched_at` (the instant the venue was
 * actually polled), never from the client clock guessing off `updated_at`.
 */
export function krakenBalanceView(portfolio: Portfolio, now: number): KrakenBalanceView {
  const amount = portfolio.kraken_balance_usd;
  const venue = VENUE_LABEL[portfolio.balance_source] ?? 'Kraken';

  if (amount == null || portfolio.balance_source === 'unavailable') {
    return {
      value: (
        <Box
          component="span"
          sx={{ display: 'inline-flex', alignItems: 'baseline', gap: 0.75, color: 'text.disabled' }}
        >
          <span>—</span>
          <Box component="span" sx={{ fontSize: 12, fontWeight: 600, letterSpacing: 0.2 }}>
            · non connecté
          </Box>
        </Box>
      ),
      note: (
        <Box component="span" sx={{ color: 'text.disabled' }}>
          Aucun instantané Kraken
        </Box>
      ),
    };
  }

  const age = portfolio.balance_fetched_at ? fmtRelative(portfolio.balance_fetched_at, now) : null;

  if (portfolio.balance_stale) {
    return {
      value: (
        <Box component="span" sx={{ color: 'text.disabled' }}>
          {fmtUsd(amount)}
        </Box>
      ),
      note: (
        <Box component="span" sx={{ color: 'warning.main' }}>
          {age ? `Périmé · ${age}` : 'Périmé'}
        </Box>
      ),
    };
  }

  return {
    value: fmtUsd(amount),
    note: <>{age ? `${venue} · ${age}` : venue}</>,
  };
}
