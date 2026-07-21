'use client';

import { useState } from 'react';
import { Box, Card, CardContent, Skeleton, Stack, ToggleButton, ToggleButtonGroup, Typography } from '@mui/material';
import { useQuery } from '@tanstack/react-query';
import { portfolioApi } from '@/lib/api/endpoints';
import { PriceAreaChart } from '@/components/charts/PriceAreaChart';
import { EmptyState } from '@/components/common';

type Range = '7d' | '30d' | '90d';

const RANGES: { label: string; value: Range }[] = [
  { label: '7j', value: '7d' },
  { label: '30j', value: '30d' },
  { label: '90j', value: '90d' },
];

export function PortfolioHistoryChart() {
  const [range, setRange] = useState<Range>('30d');

  const { data, isLoading } = useQuery({
    queryKey: ['portfolio', 'history', range],
    queryFn: () => portfolioApi.history(range),
    refetchInterval: 60_000,
  });

  return (
    <Card sx={{ height: '100%' }}>
      <CardContent>
        <Stack direction="row" justifyContent="space-between" alignItems="center" sx={{ mb: 2 }}>
          <Typography variant="subtitle2" color="text.secondary" sx={{ textTransform: 'uppercase', letterSpacing: 0.6 }}>
            Historique de valeur
          </Typography>
          <ToggleButtonGroup
            size="small"
            exclusive
            value={range}
            onChange={(_, v) => v && setRange(v as Range)}
          >
            {RANGES.map((r) => (
              <ToggleButton key={r.value} value={r.value} sx={{ px: 1.5, py: 0.25, fontSize: 11 }}>
                {r.label}
              </ToggleButton>
            ))}
          </ToggleButtonGroup>
        </Stack>

        {isLoading ? (
          <Skeleton variant="rectangular" height={260} sx={{ borderRadius: 1 }} />
        ) : !data || data.length === 0 ? (
          <EmptyState message="Aucune donnée d'historique disponible" />
        ) : (
          <PriceAreaChart data={data} height={260} color="#5b8def" dataKey="price" />
        )}
      </CardContent>
    </Card>
  );
}
