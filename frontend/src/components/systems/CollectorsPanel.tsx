'use client';

import { useState } from 'react';
import { Box, Chip, Stack, ToggleButton, ToggleButtonGroup, Tooltip, Typography } from '@mui/material';
import KeyIcon from '@mui/icons-material/Key';
import { HEALTH_COLOR, HealthDot, MiniBar } from './common';
import { HEALTH_LABEL, type CollectorSource } from '@/lib/types/systems';

const CAT_LABEL: Record<CollectorSource['category'], string> = {
  social: 'Social',
  news: 'News',
  market: 'Marché',
};

function ago(s: number) {
  if (s <= 0) return '—';
  if (s < 60) return `${s}s`;
  if (s < 3600) return `${Math.round(s / 60)}min`;
  return `${Math.round(s / 3600)}h`;
}

export function CollectorsPanel({ collectors }: { collectors: CollectorSource[] }) {
  const [filter, setFilter] = useState<'all' | CollectorSource['category']>('all');
  const shown = filter === 'all' ? collectors : collectors.filter((c) => c.category === filter);

  return (
    <Stack spacing={1.5}>
      <ToggleButtonGroup
        size="small"
        exclusive
        value={filter}
        onChange={(_, v) => v && setFilter(v)}
        sx={{ alignSelf: 'flex-start', '& .MuiToggleButton-root': { px: 1.5, py: 0.4, fontSize: 11, textTransform: 'none' } }}
      >
        <ToggleButton value="all">Tout</ToggleButton>
        <ToggleButton value="social">Social</ToggleButton>
        <ToggleButton value="news">News</ToggleButton>
        <ToggleButton value="market">Marché</ToggleButton>
      </ToggleButtonGroup>

      <Box
        sx={{
          display: 'grid',
          gap: 1.25,
          gridTemplateColumns: { xs: '1fr', sm: 'repeat(2, 1fr)' },
        }}
      >
        {shown.map((c) => {
          const rlColor = c.rate_limit_pct > 80 ? '#ff5370' : c.rate_limit_pct > 60 ? '#ffb547' : '#26d07c';
          const off = !c.enabled;
          return (
            <Box
              key={c.platform}
              sx={{
                p: 1.5,
                borderRadius: 2.5,
                border: '1px solid',
                borderColor: 'divider',
                opacity: off ? 0.5 : 1,
                background: 'rgba(255,255,255,0.015)',
              }}
            >
              <Stack direction="row" justifyContent="space-between" alignItems="center" sx={{ mb: 1 }}>
                <Stack direction="row" spacing={1} alignItems="center" sx={{ minWidth: 0 }}>
                  <HealthDot status={c.status} size={8} />
                  <Typography sx={{ fontWeight: 700, fontSize: 13 }} noWrap>
                    {c.platform}
                  </Typography>
                  {c.key_gated && (
                    <Tooltip title="Source activée par clé API">
                      <KeyIcon sx={{ fontSize: 13, color: '#ffb547' }} />
                    </Tooltip>
                  )}
                </Stack>
                <Chip label={CAT_LABEL[c.category]} size="small" variant="outlined" sx={{ height: 18, fontSize: 9.5 }} />
              </Stack>

              {off ? (
                <Typography variant="caption" color="text.secondary">
                  {HEALTH_LABEL[c.status]} — {c.key_gated ? 'clé requise' : 'désactivé'}
                </Typography>
              ) : (
                <>
                  <Stack direction="row" justifyContent="space-between" sx={{ mb: 0.75 }}>
                    <Typography className="mono" variant="caption" sx={{ color: HEALTH_COLOR[c.status], fontWeight: 700 }}>
                      {c.items_last_hour} items/h
                    </Typography>
                    <Typography variant="caption" color="text.secondary">
                      poll {c.poll_interval_s}s · dernier {ago(c.last_item_ago_s)}
                    </Typography>
                  </Stack>
                  <Stack direction="row" spacing={1} alignItems="center">
                    <Typography variant="caption" color="text.secondary" sx={{ fontSize: 10, minWidth: 62 }}>
                      quota {c.rate_limit_pct}%
                    </Typography>
                    <Box sx={{ flex: 1 }}>
                      <MiniBar pct={c.rate_limit_pct} color={rlColor} height={4} />
                    </Box>
                  </Stack>
                </>
              )}
            </Box>
          );
        })}
      </Box>
    </Stack>
  );
}
