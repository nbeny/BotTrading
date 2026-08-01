'use client';

import { Stack, Typography } from '@mui/material';
import type { TokenExposure } from '@/lib/types/dossier';
import { DeltaText, EmptyState } from '@/components/common';
import { fmtUsd } from '@/lib/format';

interface Props {
  exposure: TokenExposure;
}

/** Positions ouvertes et trades récents sur ce symbole uniquement. */
export function TokenExposurePanel({ exposure }: Props) {
  const { open_positions: positions, recent_trades: trades } = exposure;

  if (positions.length === 0 && trades.length === 0) {
    return <EmptyState message="Aucune position ouverte, aucun trade récent." />;
  }

  return (
    <Stack spacing={1.5}>
      {positions.map((p) => (
        <Stack
          key={p.position_id}
          direction="row"
          justifyContent="space-between"
          alignItems="center"
        >
          <Typography variant="body2" className="mono">
            {p.direction.toUpperCase()} · {p.quantity} @ {fmtUsd(p.entry_price)}
          </Typography>
          <DeltaText value={p.unrealized_pnl_pct} variant="body2" />
        </Stack>
      ))}

      {trades.length > 0 && (
        <Typography variant="caption" color="text.secondary">
          {trades.length} trade{trades.length > 1 ? 's' : ''} sur ce symbole
        </Typography>
      )}
    </Stack>
  );
}
