'use client';
import { useQuery } from '@tanstack/react-query';
import { Stack, Typography, Box } from '@mui/material';
import { SectionCard, Sparkline } from '@/components/systems/common';
import { portfolioApi } from '@/lib/api/endpoints';
import { pnlColor } from '@/theme/theme';

export function LivePnlPanel() {
  const { data } = useQuery({ queryKey: ['positions'], queryFn: portfolioApi.positions, refetchInterval: 5000 });
  return (
    <SectionCard title="PnL & positions live" accent="#26d07c" noPad>
      <Box sx={{ p: 1.5 }}>
        {(data ?? []).map((p) => (
          <Stack key={p.position_id} direction="row" justifyContent="space-between" alignItems="center" sx={{ py: 0.75 }}>
            <Typography variant="body2"><b>{p.symbol}</b> {p.direction} · {p.quantity}</Typography>
            <Stack direction="row" spacing={1} alignItems="center">
              <Sparkline data={Array.from({ length: 10 }, (_, i) => p.unrealized_pnl_usd * (0.6 + 0.08 * i))} color={pnlColor(p.unrealized_pnl_usd)} width={60} height={20} />
              <Typography className="mono" sx={{ fontWeight: 700, color: pnlColor(p.unrealized_pnl_usd), minWidth: 64, textAlign: 'right' }}>
                {p.unrealized_pnl_usd >= 0 ? '+' : ''}${p.unrealized_pnl_usd}
              </Typography>
            </Stack>
          </Stack>
        ))}
      </Box>
    </SectionCard>
  );
}
