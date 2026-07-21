'use client';

import { useEffect, useState } from 'react';
import {
  Alert,
  Box,
  Card,
  CardContent,
  Chip,
  Skeleton,
  Stack,
  Typography,
} from '@mui/material';
import InfoIcon from '@mui/icons-material/Info';
import WarningAmberIcon from '@mui/icons-material/WarningAmber';
import ErrorIcon from '@mui/icons-material/Error';
import CheckCircleIcon from '@mui/icons-material/CheckCircle';
import { useEventSubscription } from '@/lib/ws/WebSocketProvider';
import { fmtRelative } from '@/lib/format';
import { EmptyState } from '@/components/common';
import type { AlertLevel, RiskAlert } from '@/lib/types/domain';
import type { RiskApprovedEvent } from '@/lib/types/events';

interface Props {
  alerts: RiskAlert[] | undefined;
  isLoading: boolean;
}

const LEVEL_COLOR: Record<AlertLevel, 'info' | 'warning' | 'error'> = {
  info: 'info',
  warning: 'warning',
  critical: 'error',
};

const LEVEL_ICON: Record<AlertLevel, React.ReactNode> = {
  info: <InfoIcon fontSize="small" />,
  warning: <WarningAmberIcon fontSize="small" />,
  critical: <ErrorIcon fontSize="small" />,
};

const LEVEL_LABEL: Record<AlertLevel, string> = {
  info: 'INFO',
  warning: 'ATTENTION',
  critical: 'CRITIQUE',
};

export function RiskAlertsPanel({ alerts, isLoading }: Props) {
  const [now, setNow] = useState(Date.now());
  const [lastApproval, setLastApproval] = useState<RiskApprovedEvent | null>(null);

  // Refresh relative times every 30s
  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 30_000);
    return () => clearInterval(id);
  }, []);

  // Subscribe to live RiskApprovedEvent
  useEventSubscription(['RiskApprovedEvent'], (event) => {
    setLastApproval(event as RiskApprovedEvent);
  });

  if (isLoading) {
    return (
      <Card>
        <CardContent>
          <Skeleton variant="text" width={160} height={28} />
          <Stack spacing={1.5} sx={{ mt: 2 }}>
            {Array.from({ length: 3 }).map((_, i) => (
              <Skeleton key={i} variant="rectangular" height={60} sx={{ borderRadius: 1 }} />
            ))}
          </Stack>
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardContent>
        <Typography variant="subtitle1" fontWeight={700} gutterBottom>
          Alertes sécurité
        </Typography>

        {/* Live approval banner */}
        {lastApproval && (
          <Alert
            icon={<CheckCircleIcon fontSize="small" />}
            severity="success"
            sx={{ mb: 2, fontSize: 12 }}
          >
            <strong>Dernière approbation risque</strong> — {lastApproval.symbol}{' '}
            {lastApproval.direction.toUpperCase()} | RRR:{' '}
            {lastApproval.risk_reward_ratio.toFixed(2)} | Taille:{' '}
            {lastApproval.position_size_pct.toFixed(1)}%
          </Alert>
        )}

        {!alerts || alerts.length === 0 ? (
          <EmptyState message="Aucune alerte active." />
        ) : (
          <Stack spacing={1.5}>
            {alerts.map((alert) => (
              <Box
                key={alert.id}
                sx={{
                  p: 1.5,
                  borderRadius: 1.5,
                  border: '1px solid',
                  borderColor:
                    alert.level === 'critical'
                      ? 'error.dark'
                      : alert.level === 'warning'
                      ? 'warning.dark'
                      : 'info.dark',
                  background:
                    alert.level === 'critical'
                      ? 'rgba(239,68,68,0.06)'
                      : alert.level === 'warning'
                      ? 'rgba(251,191,36,0.06)'
                      : 'rgba(59,130,246,0.06)',
                }}
              >
                <Stack direction="row" spacing={1} alignItems="flex-start">
                  <Box
                    sx={{
                      color:
                        alert.level === 'critical'
                          ? 'error.main'
                          : alert.level === 'warning'
                          ? 'warning.main'
                          : 'info.main',
                      mt: 0.1,
                      flexShrink: 0,
                    }}
                  >
                    {LEVEL_ICON[alert.level]}
                  </Box>
                  <Box sx={{ flex: 1, minWidth: 0 }}>
                    <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap" gap={0.5}>
                      <Chip
                        label={LEVEL_LABEL[alert.level]}
                        size="small"
                        color={LEVEL_COLOR[alert.level]}
                        variant="outlined"
                        sx={{ fontSize: 10, height: 18, fontWeight: 700 }}
                      />
                      {alert.symbol && (
                        <Chip
                          label={alert.symbol}
                          size="small"
                          variant="filled"
                          sx={{ fontSize: 10, height: 18 }}
                        />
                      )}
                      <Typography variant="caption" color="text.disabled" sx={{ ml: 'auto' }}>
                        {fmtRelative(alert.created_at, now)}
                      </Typography>
                    </Stack>
                    <Typography variant="body2" sx={{ mt: 0.5, lineHeight: 1.4 }}>
                      {alert.message}
                    </Typography>
                  </Box>
                </Stack>
              </Box>
            ))}
          </Stack>
        )}
      </CardContent>
    </Card>
  );
}
