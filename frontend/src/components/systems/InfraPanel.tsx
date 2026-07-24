'use client';

import { Box, Stack, Typography } from '@mui/material';
import StorageIcon from '@mui/icons-material/Storage';
import MemoryIcon from '@mui/icons-material/Memory';
import HubIcon from '@mui/icons-material/Hub';
import RouterIcon from '@mui/icons-material/Router';
import { HealthDot, MiniBar } from './common';
import type { InfraResource } from '@/lib/types/systems';

const ICON: Record<InfraResource['kind'], React.ReactNode> = {
  database: <StorageIcon sx={{ fontSize: 18 }} />,
  cache: <MemoryIcon sx={{ fontSize: 18 }} />,
  broker: <HubIcon sx={{ fontSize: 18 }} />,
  proxy: <RouterIcon sx={{ fontSize: 18 }} />,
};

export function InfraPanel({ infra }: { infra: InfraResource[] }) {
  return (
    <Box
      sx={{
        display: 'grid',
        gap: 1.5,
        gridTemplateColumns: { xs: '1fr', sm: 'repeat(2, 1fr)' },
      }}
    >
      {infra.map((r) => (
        <Box
          key={r.id}
          sx={{
            p: 1.75,
            borderRadius: 3,
            border: '1px solid',
            borderColor: 'divider',
            background: 'rgba(255,255,255,0.015)',
          }}
        >
          <Stack direction="row" alignItems="center" spacing={1.25} sx={{ mb: 1.5 }}>
            <Box sx={{ width: 30, height: 30, borderRadius: 2, display: 'grid', placeItems: 'center', bgcolor: 'rgba(34,211,238,0.12)', color: '#22d3ee' }}>
              {ICON[r.kind]}
            </Box>
            <Typography sx={{ fontWeight: 700, fontSize: 13.5, flex: 1 }} noWrap>
              {r.name}
            </Typography>
            <HealthDot status={r.status} size={8} />
          </Stack>

          <Stack spacing={1}>
            {r.metrics.map((m) => (
              <Box key={m.label}>
                <Stack direction="row" justifyContent="space-between" sx={{ mb: m.pct != null ? 0.4 : 0 }}>
                  <Typography variant="caption" color="text.secondary">
                    {m.label}
                  </Typography>
                  <Typography variant="caption" className="mono" sx={{ fontWeight: 700 }}>
                    {m.value}
                  </Typography>
                </Stack>
                {m.pct != null && <MiniBar pct={m.pct} color="#22d3ee" height={3} />}
              </Box>
            ))}
          </Stack>
        </Box>
      ))}
    </Box>
  );
}
