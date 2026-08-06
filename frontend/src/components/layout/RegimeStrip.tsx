'use client';

import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Box, ButtonBase, Chip, Popover, Stack, Typography } from '@mui/material';
import { regimeApi, tradingApi } from '@/lib/api/endpoints';
import { DRIVER_LABELS, REGIME_LABELS, type DriverKey, type DriverState, type RegimeDriver } from '@/lib/types/regime';
import { fmtRelative } from '@/lib/format';

const STATE_COLOR: Record<DriverState, string> = {
  bullish: 'success.main',
  bearish: 'error.main',
  neutral: 'text.primary',
};
const STATE_ARROW: Record<DriverState, string> = { bullish: '▲', bearish: '▼', neutral: '·' };

function driverValue(d: RegimeDriver): string {
  if (d.value === null) return '—';
  switch (d.key) {
    case 'funding':
      return `${(d.value * 100).toFixed(4)}%/8h`;
    case 'oi_delta':
      return `${d.value > 0 ? '+' : ''}${d.value.toFixed(1)}%`;
    case 'market_sentiment':
      return d.value.toFixed(2);
    case 'btc_dominance':
      return `${d.value > 0 ? '+' : ''}${d.value.toFixed(2)} pt`;
    case 'breadth':
      return `${Math.round(d.value * 100)}%`;
  }
}

export function RegimeStrip() {
  const regime = useQuery({ queryKey: ['market', 'regime'], queryFn: regimeApi.get, refetchInterval: 30_000 });
  const status = useQuery({ queryKey: ['trading', 'status'], queryFn: tradingApi.status, refetchInterval: 15_000 });
  const [anchor, setAnchor] = useState<{ el: HTMLElement; key: DriverKey } | null>(null);

  const data = regime.data;
  // Derived live from the latest fetch, never frozen at click time — a 30s
  // poll must not leave an open popover showing a stale value/detail/as_of.
  const anchorDriver = anchor ? (data?.drivers ?? []).find((d) => d.key === anchor.key) : undefined;
  return (
    <Box
      className="cmi-glass mono"
      sx={{
        borderRadius: 2,
        px: 1.5,
        py: 0.75,
        mb: 2,
        display: 'flex',
        alignItems: 'center',
        gap: 2,
        overflowX: 'auto',
        whiteSpace: 'nowrap',
        fontSize: 13,
      }}
    >
      <Stack direction="row" spacing={1} alignItems="center">
        <Typography variant="caption" sx={{ opacity: 0.6, letterSpacing: 1 }}>
          RÉGIME
        </Typography>
        <Typography data-testid="regime-label" sx={{ fontWeight: 700 }}>
          {data?.regime ? REGIME_LABELS[data.regime] : '—'}
        </Typography>
        <Typography variant="caption" sx={{ opacity: 0.6 }}>
          conf {data?.confidence !== null && data?.confidence !== undefined ? `${Math.round(data.confidence * 100)}%` : '—'}
        </Typography>
      </Stack>
      {(data?.drivers ?? []).map((d) => (
        <ButtonBase
          key={d.key}
          data-testid={`driver-${d.key}`}
          onClick={(e) => setAnchor({ el: e.currentTarget, key: d.key })}
          sx={{
            cursor: 'pointer',
            borderLeft: '1px solid',
            borderColor: 'divider',
            pl: 2,
            pr: 1,
            opacity: d.state === null ? 0.5 : 1,
            borderRadius: 1,
          }}
        >
          <Typography variant="caption" sx={{ opacity: 0.6, mr: 0.5 }}>
            {DRIVER_LABELS[d.key]}
          </Typography>
          <Typography component="span" sx={{ color: d.state ? STATE_COLOR[d.state] : 'text.primary' }}>
            {d.state ? `${STATE_ARROW[d.state]} ` : ''}
            {driverValue(d)}
          </Typography>
        </ButtonBase>
      ))}
      <Box sx={{ ml: 'auto', display: 'flex', gap: 1 }}>
        <Chip size="small" variant="outlined" label={status.data?.mode ?? '—'} />
        <Chip
          size="small"
          variant="outlined"
          color={status.data && !status.data.trading_enabled ? 'error' : 'default'}
          label={status.data ? (status.data.trading_enabled ? 'kill:off' : 'kill:on') : 'kill:—'}
        />
      </Box>
      <Popover
        open={!!anchor && !!anchorDriver}
        anchorEl={anchor?.el ?? null}
        onClose={() => setAnchor(null)}
        anchorOrigin={{ vertical: 'bottom', horizontal: 'left' }}
      >
        {anchorDriver && (
          <Box sx={{ p: 1.5, maxWidth: 380 }}>
            <Typography variant="subtitle2">{DRIVER_LABELS[anchorDriver.key]}</Typography>
            <Typography variant="body2" sx={{ mt: 0.5 }}>
              {anchorDriver.detail}
            </Typography>
            <Typography variant="caption" sx={{ opacity: 0.6 }}>
              {anchorDriver.state === null
                ? 'non mesuré — exclu de l’agrégat'
                : anchorDriver.as_of
                  ? `mesuré ${fmtRelative(anchorDriver.as_of, Date.now())}`
                  : 'fraîcheur inconnue'}
            </Typography>
          </Box>
        )}
      </Popover>
    </Box>
  );
}
