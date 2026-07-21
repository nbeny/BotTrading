'use client';

import { useState } from 'react';
import {
  Box,
  Card,
  CardContent,
  Skeleton,
  Stack,
  ToggleButton,
  ToggleButtonGroup,
  Typography,
} from '@mui/material';
import { useQuery } from '@tanstack/react-query';
import { marketApi } from '@/lib/api/endpoints';
import type { MarketToken } from '@/lib/types/domain';
import { PriceAreaChart } from '@/components/charts/PriceAreaChart';
import { DeltaText, EmptyState, SentimentChip } from '@/components/common';
import { fmtUsd, fmtUsdCompact } from '@/lib/format';

type Range = '1d' | '7d' | '30d';

interface StatItemProps {
  label: string;
  value: React.ReactNode;
}

function StatItem({ label, value }: StatItemProps) {
  return (
    <Box>
      <Typography variant="caption" color="text.secondary" sx={{ textTransform: 'uppercase', letterSpacing: 0.5, display: 'block' }}>
        {label}
      </Typography>
      <Box sx={{ mt: 0.25 }}>{value}</Box>
    </Box>
  );
}

interface Props {
  token: MarketToken;
}

export function TokenPricePanel({ token }: Props) {
  const [range, setRange] = useState<Range>('1d');

  const { data: prices = [], isLoading } = useQuery({
    queryKey: ['market', 'prices', token.symbol, range],
    queryFn: () => marketApi.prices(token.symbol, range),
    refetchInterval: 60_000,
  });

  return (
    <Card sx={{ height: '100%' }}>
      <CardContent>
        {/* Header */}
        <Stack direction="row" justifyContent="space-between" alignItems="flex-start" flexWrap="wrap" gap={1} sx={{ mb: 2 }}>
          <Box>
            <Typography variant="h6" sx={{ fontWeight: 700 }}>
              {token.symbol}
              <Typography component="span" variant="body2" color="text.secondary" sx={{ ml: 1 }}>
                {token.name}
              </Typography>
            </Typography>
            <Typography variant="h5" className="mono" sx={{ fontWeight: 800, mt: 0.25 }}>
              {fmtUsd(token.price_usd)}
            </Typography>
          </Box>
          <ToggleButtonGroup
            size="small"
            exclusive
            value={range}
            onChange={(_, v: Range | null) => v && setRange(v)}
          >
            {(['1d', '7d', '30d'] as Range[]).map((r) => (
              <ToggleButton key={r} value={r} sx={{ px: 1.5, py: 0.25, fontSize: 12 }}>
                {r}
              </ToggleButton>
            ))}
          </ToggleButtonGroup>
        </Stack>

        {/* Stat strip */}
        <Box
          sx={{
            display: 'grid',
            gridTemplateColumns: 'repeat(auto-fit, minmax(100px, 1fr))',
            gap: 2,
            mb: 2,
            p: 1.5,
            borderRadius: 1.5,
            bgcolor: 'rgba(255,255,255,0.03)',
            border: '1px solid rgba(255,255,255,0.06)',
          }}
        >
          <StatItem
            label="Variation 24h"
            value={<DeltaText value={token.price_change_pct_24h} variant="body2" />}
          />
          <StatItem
            label="Volume 24h"
            value={
              <Typography variant="body2" className="mono">
                {fmtUsdCompact(token.volume_24h_usd)}
              </Typography>
            }
          />
          <StatItem
            label="Liquidité"
            value={
              <Typography variant="body2" className="mono">
                {fmtUsdCompact(token.liquidity_usd)}
              </Typography>
            }
          />
          <StatItem
            label="Sentiment"
            value={<SentimentChip score={token.sentiment_score} />}
          />
        </Box>

        {/* Chart */}
        {isLoading ? (
          <Skeleton variant="rectangular" height={240} sx={{ borderRadius: 1 }} />
        ) : prices.length === 0 ? (
          <EmptyState message="Pas de données de prix disponibles." />
        ) : (
          <PriceAreaChart data={prices} height={240} color="#5b8def" dataKey="price" />
        )}
      </CardContent>
    </Card>
  );
}
