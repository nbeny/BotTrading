'use client';
import { useQuery } from '@tanstack/react-query';
import { Stack, Typography, Box, Chip } from '@mui/material';
import { SectionCard, MiniBar } from '@/components/systems/common';
import { riskApi, tradingApi } from '@/lib/api/endpoints';

export function GuardrailPanel() {
  const exp = useQuery({ queryKey: ['risk', 'exposure'], queryFn: riskApi.exposure, refetchInterval: 8000 });
  const st = useQuery({ queryKey: ['trading', 'status'], queryFn: tradingApi.status, refetchInterval: 10000 });
  const e = exp.data;
  return (
    <SectionCard title="Garde-fous" accent="#ffb547">
      <Stack spacing={1.5}>
        <Box>
          <Stack direction="row" justifyContent="space-between"><Typography variant="caption" color="text.secondary">Exposition</Typography>
            <Typography variant="caption" className="mono">{e?.total_exposure_pct ?? 0}% / {e?.max_exposure_pct ?? 80}%</Typography></Stack>
          <MiniBar pct={e ? (e.total_exposure_pct / e.max_exposure_pct) * 100 : 0} color="#ffb547" />
        </Box>
        <Box>
          <Stack direction="row" justifyContent="space-between"><Typography variant="caption" color="text.secondary">Perte jour</Typography>
            <Typography variant="caption" className="mono">${e?.daily_loss_usd ?? 0} / ${e?.daily_loss_limit_usd ?? 0}</Typography></Stack>
          <MiniBar pct={e ? (e.daily_loss_usd / e.daily_loss_limit_usd) * 100 : 0} color="#ff5370" />
        </Box>
        <Chip label={st.data?.trading_enabled ? 'Kill-switch OFF' : 'KILL-SWITCH ON'} color={st.data?.trading_enabled ? 'success' : 'error'}
          size="small" variant="outlined" sx={{ alignSelf: 'flex-start' }} />
      </Stack>
    </SectionCard>
  );
}
