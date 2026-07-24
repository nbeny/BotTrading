'use client';
import { Box, Stack, Typography } from '@mui/material';
import { pnlColor } from '@/theme/theme';
import { HealthDot } from '@/components/systems/common';
import type { Portfolio, RiskExposure, TradingStatus } from '@/lib/types/domain';

function Cell({ label, value, color }: { label: string; value: React.ReactNode; color?: string }) {
  return (
    <Box sx={{ px: 2, py: 1, borderRight: '1px solid', borderColor: 'divider', minWidth: 120, flex: '1 0 auto' }}>
      <Typography className="mono" sx={{ fontWeight: 800, fontSize: 16, color: color ?? 'text.primary', lineHeight: 1.1 }}>{value}</Typography>
      <Typography variant="caption" color="text.secondary" sx={{ textTransform: 'uppercase', letterSpacing: 0.5, fontSize: 9.5 }}>{label}</Typography>
    </Box>
  );
}

export function KpiTicker({ portfolio, status, exposure, eventsPerMin }: {
  portfolio?: Portfolio; status?: TradingStatus; exposure?: RiskExposure; eventsPerMin: number;
}) {
  const live = status?.mode === 'live';
  return (
    <Box className="cmi-glass reveal" sx={{ borderRadius: 3, display: 'flex', overflowX: 'auto', '&::-webkit-scrollbar': { height: 5 } }}>
      <Cell label="Portefeuille" value={portfolio ? `$${portfolio.total_value_usd.toLocaleString('fr-FR')}` : '—'} color={pnlColor(portfolio?.pnl_24h_pct ?? 0)} />
      <Cell label="PnL jour" value={portfolio ? `${portfolio.realized_pnl_24h_usd >= 0 ? '+' : ''}$${portfolio.realized_pnl_24h_usd}` : '—'} color={pnlColor(portfolio?.realized_pnl_24h_usd ?? 0)} />
      <Cell label="Exposition" value={exposure ? `${exposure.total_exposure_pct}%` : '—'} />
      <Cell label="Positions" value={exposure?.open_positions ?? '—'} />
      <Cell label={live ? 'LIVE · auto' : 'PAPER · auto'} value={status?.auto_trading_enabled ? 'AUTO' : 'MANUEL'} color={live ? '#ff5370' : '#ffb547'} />
      <Cell label="evt/min" value={eventsPerMin} color="#4d9fff" />
      <Box sx={{ px: 2, py: 1, display: 'flex', alignItems: 'center', gap: 1 }}>
        <HealthDot status={status?.trading_enabled ? 'healthy' : 'down'} />
        <Typography variant="caption" color="text.secondary">Kraken · Kafka</Typography>
      </Box>
    </Box>
  );
}
