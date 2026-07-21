'use client';

import {
  Card,
  CardContent,
  Chip,
  Skeleton,
  Stack,
  Typography,
} from '@mui/material';
import ErrorOutlineIcon from '@mui/icons-material/ErrorOutline';
import WarningAmberIcon from '@mui/icons-material/WarningAmber';
import InfoOutlinedIcon from '@mui/icons-material/InfoOutlined';
import { EmptyState } from '@/components/common';
import { fmtRelative } from '@/lib/format';
import type { RiskAlert, AlertLevel } from '@/lib/types/domain';

interface RiskAlertsProps {
  alerts: RiskAlert[] | undefined;
  isLoading: boolean;
  now: number;
}

const ALERT_META: Record<AlertLevel, {
  color: 'error' | 'warning' | 'info';
  icon: React.ReactNode;
  bg: string;
}> = {
  critical: { color: 'error', icon: <ErrorOutlineIcon fontSize="small" />, bg: 'rgba(255,83,112,0.08)' },
  warning: { color: 'warning', icon: <WarningAmberIcon fontSize="small" />, bg: 'rgba(255,181,71,0.08)' },
  info: { color: 'info', icon: <InfoOutlinedIcon fontSize="small" />, bg: 'rgba(91,141,239,0.06)' },
};

export function RiskAlerts({ alerts, isLoading, now }: RiskAlertsProps) {
  return (
    <Card>
      <CardContent>
        <Typography variant="h6" sx={{ mb: 2 }}>
          Alertes Risques
        </Typography>

        {isLoading ? (
          <Stack spacing={1}>
            {Array.from({ length: 3 }).map((_, i) => (
              <Skeleton key={i} variant="rectangular" height={56} sx={{ borderRadius: 1.5 }} />
            ))}
          </Stack>
        ) : !alerts || alerts.length === 0 ? (
          <EmptyState message="Aucune alerte risque active." />
        ) : (
          <Stack spacing={1}>
            {alerts.map((alert) => {
              const meta = ALERT_META[alert.level] ?? ALERT_META.info;
              return (
                <Stack
                  key={alert.id}
                  direction="row"
                  spacing={1.5}
                  alignItems="flex-start"
                  sx={{
                    p: 1.5,
                    borderRadius: 1.5,
                    background: meta.bg,
                    border: `1px solid`,
                    borderColor: `${meta.color}.main`,
                    opacity: 0.85,
                  }}
                >
                  <Stack direction="row" alignItems="center" spacing={0.5} sx={{ color: `${meta.color}.main`, mt: 0.1, flexShrink: 0 }}>
                    {meta.icon}
                    {alert.symbol && (
                      <Chip label={alert.symbol} size="small" color={meta.color} variant="outlined" sx={{ fontSize: 10, height: 18 }} />
                    )}
                  </Stack>
                  <Stack flex={1}>
                    <Typography variant="body2" sx={{ fontSize: 13 }}>
                      {alert.message}
                    </Typography>
                    <Typography variant="caption" color="text.secondary" className="mono">
                      {fmtRelative(alert.created_at, now)}
                    </Typography>
                  </Stack>
                  <Chip
                    label={alert.level.toUpperCase()}
                    color={meta.color}
                    size="small"
                    sx={{ flexShrink: 0, fontSize: 10, height: 20 }}
                  />
                </Stack>
              );
            })}
          </Stack>
        )}
      </CardContent>
    </Card>
  );
}
