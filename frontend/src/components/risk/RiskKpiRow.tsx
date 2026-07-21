'use client';

import { Box, Card, CardContent, LinearProgress, Skeleton, Stack, Tooltip, Typography } from '@mui/material';
import ShieldIcon from '@mui/icons-material/Shield';
import TrendingDownIcon from '@mui/icons-material/TrendingDown';
import AccountBalanceWalletIcon from '@mui/icons-material/AccountBalanceWallet';
import WarningAmberIcon from '@mui/icons-material/WarningAmber';
import { fmtUsd, fmtPct } from '@/lib/format';
import type { RiskExposure } from '@/lib/types/domain';

interface Props {
  data: RiskExposure | undefined;
  isLoading: boolean;
}

function KpiCard({
  label,
  value,
  footnote,
  icon,
  accent,
  progress,
  progressColor,
}: {
  label: string;
  value: React.ReactNode;
  footnote?: React.ReactNode;
  icon?: React.ReactNode;
  accent?: string;
  progress?: number;
  progressColor?: 'primary' | 'error' | 'warning' | 'success';
}) {
  return (
    <Card sx={{ height: '100%' }}>
      <CardContent>
        <Stack direction="row" justifyContent="space-between" alignItems="flex-start">
          <Typography
            variant="caption"
            color="text.secondary"
            sx={{ textTransform: 'uppercase', letterSpacing: 0.6 }}
          >
            {label}
          </Typography>
          {icon && (
            <Box sx={{ color: accent ?? 'primary.main', opacity: 0.85 }}>{icon}</Box>
          )}
        </Stack>
        <Typography variant="h5" className="mono" sx={{ mt: 1, fontWeight: 800 }}>
          {value}
        </Typography>
        {footnote && (
          <Typography variant="caption" color="text.secondary" sx={{ mt: 0.5, display: 'block' }}>
            {footnote}
          </Typography>
        )}
        {progress != null && (
          <Tooltip title={`${progress.toFixed(1)}%`}>
            <LinearProgress
              variant="determinate"
              value={Math.min(progress, 100)}
              color={progressColor ?? 'primary'}
              sx={{ mt: 1.5, height: 6, borderRadius: 3 }}
            />
          </Tooltip>
        )}
      </CardContent>
    </Card>
  );
}

export function RiskKpiRow({ data, isLoading }: Props) {
  if (isLoading || !data) {
    return (
      <Box
        sx={{
          display: 'grid',
          gap: 2,
          gridTemplateColumns: { xs: '1fr', sm: '1fr 1fr', lg: 'repeat(4, 1fr)' },
        }}
      >
        {Array.from({ length: 4 }).map((_, i) => (
          <Skeleton key={i} variant="rectangular" height={130} sx={{ borderRadius: 2 }} />
        ))}
      </Box>
    );
  }

  const utilisationPct = data.max_exposure_pct > 0
    ? (data.total_exposure_pct / data.max_exposure_pct) * 100
    : 0;
  const utilisationColor = utilisationPct > 80 ? 'error' : utilisationPct > 60 ? 'warning' : 'primary';

  const dailyLossPct = data.daily_loss_limit_usd > 0
    ? (Math.abs(data.daily_loss_usd) / data.daily_loss_limit_usd) * 100
    : 0;
  const dailyLossColor = dailyLossPct > 80 ? 'error' : dailyLossPct > 50 ? 'warning' : 'success';

  const protectedCount = data.protected_positions;
  const openCount = data.open_positions;

  return (
    <Box
      sx={{
        display: 'grid',
        gap: 2,
        gridTemplateColumns: { xs: '1fr', sm: '1fr 1fr', lg: 'repeat(4, 1fr)' },
      }}
    >
      <KpiCard
        label="Exposition globale"
        value={fmtUsd(data.total_exposure_usd)}
        footnote={`${fmtPct(data.total_exposure_pct)} du portefeuille`}
        icon={<AccountBalanceWalletIcon fontSize="small" />}
        accent="info.main"
      />
      <KpiCard
        label="Utilisation limite"
        value={`${data.total_exposure_pct.toFixed(1)}% / ${data.max_exposure_pct}%`}
        footnote={utilisationPct > 80 ? '⚠ Proche de la limite' : undefined}
        icon={<WarningAmberIcon fontSize="small" />}
        accent={utilisationColor === 'error' ? 'error.main' : 'warning.main'}
        progress={utilisationPct}
        progressColor={utilisationColor as 'primary' | 'error' | 'warning'}
      />
      <KpiCard
        label="Positions protégées"
        value={`${protectedCount} / ${openCount}`}
        footnote={openCount > 0 ? `${((protectedCount / openCount) * 100).toFixed(0)}% couvertes` : 'Aucune position ouverte'}
        icon={<ShieldIcon fontSize="small" />}
        accent="success.main"
      />
      <KpiCard
        label="Perte journalière"
        value={fmtUsd(data.daily_loss_usd)}
        footnote={`Limite: ${fmtUsd(data.daily_loss_limit_usd)}`}
        icon={<TrendingDownIcon fontSize="small" />}
        accent={dailyLossColor === 'error' ? 'error.main' : 'text.secondary'}
        progress={dailyLossPct}
        progressColor={dailyLossColor as 'error' | 'warning' | 'success'}
      />
    </Box>
  );
}
