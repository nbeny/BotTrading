'use client';
import { useQuery } from '@tanstack/react-query';
import { Box, Chip } from '@mui/material';
import { SectionCard } from '@/components/systems/common';
import { marketApi } from '@/lib/api/endpoints';

export function MarketHeatPanel() {
  const { data } = useQuery({ queryKey: ['market', 'tokens'], queryFn: marketApi.tokens, refetchInterval: 7000 });
  return (
    <SectionCard title="Heat marché" accent="#22d3ee">
      <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 0.75 }}>
        {(data ?? []).slice(0, 16).map((t) => (
          <Chip key={t.symbol} size="small" label={`${t.symbol} ${t.price_change_pct_24h >= 0 ? '▲' : '▼'}${Math.abs(t.price_change_pct_24h)}`}
            color={t.price_change_pct_24h >= 0 ? 'success' : 'error'} variant="outlined" className="mono" sx={{ height: 22, fontSize: 10.5 }} />
        ))}
      </Box>
    </SectionCard>
  );
}
