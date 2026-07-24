'use client';

import { Box, Chip, Stack, Tooltip, Typography } from '@mui/material';
import { MiniBar } from './common';
import type { KafkaTopic } from '@/lib/types/systems';

function fmtBytes(n: number) {
  if (n > 1e6) return `${(n / 1e6).toFixed(1)} MB/m`;
  if (n > 1e3) return `${(n / 1e3).toFixed(0)} KB/m`;
  return `${n} B/m`;
}

export function KafkaPanel({ topics }: { topics: KafkaTopic[] }) {
  const max = Math.max(...topics.map((t) => t.msg_per_min), 1);
  return (
    <Stack spacing={0.25}>
      {topics.map((t) => {
        const lagColor = t.lag > 100 ? '#ff5370' : t.lag > 30 ? '#ffb547' : '#26d07c';
        return (
          <Box
            key={t.name}
            sx={{
              display: 'grid',
              gridTemplateColumns: { xs: '1fr auto', sm: '1.4fr 1fr auto' },
              alignItems: 'center',
              gap: 1.5,
              py: 1,
              px: 0.5,
              borderRadius: 2,
              opacity: t.orphaned ? 0.55 : 1,
              transition: 'background .15s',
              '&:hover': { bgcolor: 'rgba(255,255,255,0.02)' },
            }}
          >
            <Stack direction="row" spacing={1} alignItems="center" sx={{ minWidth: 0 }}>
              <Typography className="mono" sx={{ fontSize: 12.5, fontWeight: 600 }} noWrap>
                {t.name}
              </Typography>
              {t.orphaned && (
                <Tooltip title="Topic orphelin — plus aucun producteur/consommateur">
                  <Chip label="orphelin" size="small" sx={{ height: 17, fontSize: 9, bgcolor: 'rgba(139,151,176,0.15)' }} />
                </Tooltip>
              )}
            </Stack>

            <Box sx={{ display: { xs: 'none', sm: 'block' } }}>
              <Stack direction="row" justifyContent="space-between" sx={{ mb: 0.4 }}>
                <Typography className="mono" variant="caption" color="text.secondary">
                  {t.msg_per_min}/m
                </Typography>
                <Typography variant="caption" color="text.secondary">
                  {fmtBytes(t.bytes_per_min)}
                </Typography>
              </Stack>
              <MiniBar pct={(t.msg_per_min / max) * 100} color="#4d9fff" height={4} />
            </Box>

            <Stack direction="row" spacing={1.5} alignItems="center" justifyContent="flex-end">
              <Tooltip title="Partitions · consommateurs">
                <Typography className="mono" variant="caption" color="text.secondary">
                  {t.partitions}p · {t.consumers}c
                </Typography>
              </Tooltip>
              <Tooltip title="Lag (messages en attente)">
                <Chip
                  label={`lag ${t.lag}`}
                  size="small"
                  className="mono"
                  sx={{ height: 20, fontSize: 10.5, bgcolor: `${lagColor}1f`, color: lagColor, minWidth: 62 }}
                />
              </Tooltip>
            </Stack>
          </Box>
        );
      })}
    </Stack>
  );
}
