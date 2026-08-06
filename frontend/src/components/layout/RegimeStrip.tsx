'use client';

import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Box, Chip, Popover, Stack, Typography } from '@mui/material';
import { regimeApi, tradingApi } from '@/lib/api/endpoints';
import { DRIVER_LABELS, REGIME_LABELS, type RegimeDriver } from '@/lib/types/regime';
import { fmtRelative } from '@/lib/format';

const STATE_COLOR: Record<string, string> = {
  bullish: 'success.main',
  bearish: 'error.main',
  neutral: 'text.primary',
};
const STATE_ARROW: Record<string, string> = { bullish: '▲', bearish: '▼', neutral: '·' };

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
  const [anchor, setAnchor] = useState<{ el: HTMLElement; driver: RegimeDriver } | null>(null);

  const data = regime.data;
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
        <Box
          key={d.key}
          data-testid={`driver-${d.key}`}
          onClick={(e) => setAnchor({ el: e.currentTarget, driver: d })}
          sx={{ cursor: 'pointer', borderLeft: '1px solid', borderColor: 'divider', pl: 2, opacity: d.state === null ? 0.5 : 1 }}
        >
          <Typography variant="caption" sx={{ opacity: 0.6, mr: 0.5 }}>
            {DRIVER_LABELS[d.key]}
          </Typography>
          <Typography component="span" sx={{ color: d.state ? STATE_COLOR[d.state] : 'text.primary' }}>
            {d.state ? `${STATE_ARROW[d.state]} ` : ''}
            {driverValue(d)}
          </Typography>
        </Box>
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
        open={!!anchor}
        anchorEl={anchor?.el ?? null}
        onClose={() => setAnchor(null)}
        anchorOrigin={{ vertical: 'bottom', horizontal: 'left' }}
      >
        {anchor && (
          <Box sx={{ p: 1.5, maxWidth: 380 }}>
            <Typography variant="subtitle2">{DRIVER_LABELS[anchor.driver.key]}</Typography>
            <Typography variant="body2" sx={{ mt: 0.5 }}>
              {anchor.driver.detail}
            </Typography>
            <Typography variant="caption" sx={{ opacity: 0.6 }}>
              {anchor.driver.as_of ? `mesuré ${fmtRelative(anchor.driver.as_of, Date.now())}` : 'non mesuré — exclu de l’agrégat'}
            </Typography>
          </Box>
        )}
      </Popover>
    </Box>
  );
}
