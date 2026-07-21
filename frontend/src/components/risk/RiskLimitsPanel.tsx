'use client';

import {
  Box,
  Card,
  CardContent,
  Chip,
  LinearProgress,
  Skeleton,
  Stack,
  Typography,
} from '@mui/material';
import { EmptyState } from '@/components/common';
import type { RiskLimit } from '@/lib/types/domain';

interface Props {
  limits: RiskLimit[] | undefined;
  isLoading: boolean;
}

export function RiskLimitsPanel({ limits, isLoading }: Props) {
  if (isLoading) {
    return (
      <Card>
        <CardContent>
          <Skeleton variant="text" width={180} height={28} />
          <Stack spacing={2} sx={{ mt: 2 }}>
            {Array.from({ length: 4 }).map((_, i) => (
              <Skeleton key={i} variant="rectangular" height={56} sx={{ borderRadius: 1 }} />
            ))}
          </Stack>
        </CardContent>
      </Card>
    );
  }

  if (!limits || limits.length === 0) {
    return (
      <Card>
        <CardContent>
          <Typography variant="subtitle1" fontWeight={700} gutterBottom>
            Limites de risque
          </Typography>
          <EmptyState message="Aucune limite configurée." />
        </CardContent>
      </Card>
    );
  }

  return (
    <Card>
      <CardContent>
        <Typography variant="subtitle1" fontWeight={700} gutterBottom>
          Limites de risque
        </Typography>
        <Stack spacing={2} sx={{ mt: 1 }}>
          {limits.map((limit) => {
            const pct = limit.max > 0 ? (limit.value / limit.max) * 100 : 0;
            const progressColor = limit.breached
              ? 'error'
              : pct > 80
              ? 'warning'
              : 'primary';

            return (
              <Box key={limit.key}>
                <Stack direction="row" justifyContent="space-between" alignItems="center" sx={{ mb: 0.5 }}>
                  <Typography variant="body2" fontWeight={600}>
                    {limit.label}
                  </Typography>
                  <Stack direction="row" spacing={1} alignItems="center">
                    <Typography variant="caption" className="mono" color="text.secondary">
                      {limit.value}{limit.unit} / {limit.max}{limit.unit}
                    </Typography>
                    {limit.breached && (
                      <Chip
                        label="DÉPASSÉ"
                        size="small"
                        color="error"
                        variant="filled"
                        sx={{ fontWeight: 800, fontSize: 10, height: 18 }}
                      />
                    )}
                  </Stack>
                </Stack>
                <LinearProgress
                  variant="determinate"
                  value={Math.min(pct, 100)}
                  color={progressColor as 'primary' | 'error' | 'warning'}
                  sx={{ height: 6, borderRadius: 3 }}
                />
              </Box>
            );
          })}
        </Stack>
      </CardContent>
    </Card>
  );
}
