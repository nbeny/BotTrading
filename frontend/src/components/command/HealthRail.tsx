'use client';
import Link from 'next/link';
import { useQuery } from '@tanstack/react-query';
import { Box, Stack, Typography } from '@mui/material';
import { SectionCard, HealthDot } from '@/components/systems/common';
import { systemsApi } from '@/lib/api/endpoints';

export function HealthRail() {
  const { data } = useQuery({ queryKey: ['systems', 'overview'], queryFn: systemsApi.overview, refetchInterval: 8000 });
  return (
    <SectionCard title="Santé technique" accent="#22d3ee"
      actions={<Typography component={Link} href="/systems" variant="caption" sx={{ color: 'primary.main' }}>voir tout →</Typography>}>
      <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 1 }}>
        {(data?.services ?? []).slice(0, 10).map((s) => (
          <Stack key={s.id} direction="row" spacing={0.75} alignItems="center" sx={{ minWidth: 130 }}>
            <HealthDot status={s.status} size={8} />
            <Typography variant="caption" noWrap>{s.name}</Typography>
          </Stack>
        ))}
      </Box>
    </SectionCard>
  );
}
