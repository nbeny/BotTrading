'use client';

import { useQuery } from '@tanstack/react-query';
import { Box, LinearProgress, Stack, Typography } from '@mui/material';
import { journalApi } from '@/lib/api/endpoints';
import type { JournalWindow } from '@/lib/types/journal';

export function AttributionPanel({ window }: { window: JournalWindow }) {
  const { data } = useQuery({
    queryKey: ['journal', 'attribution', window],
    queryFn: () => journalApi.attribution(window),
  });

  return (
    <Stack spacing={1}>
      <Typography variant="caption" sx={{ opacity: 0.6 }}>
        Corrélation facteur ↔ PnL simulé @ {data?.horizon ?? '—'} — l’attribution par les 8 axes exige des décisions passées.
      </Typography>
      {(data?.factors ?? []).map((f) => (
        <Stack key={f.key} direction="row" spacing={1} alignItems="center" data-testid={`attribution-${f.key}`}>
          <Typography variant="caption" sx={{ width: 90, opacity: 0.7 }}>{f.key}</Typography>
          {f.correlation === null ? (
            <Typography variant="caption" sx={{ opacity: 0.5 }}>— (n={f.n} &lt; {data?.min_n})</Typography>
          ) : (
            <>
              <Box sx={{ flex: 1 }}>
                <LinearProgress variant="determinate" value={Math.abs(f.correlation) * 100} color={f.correlation >= 0 ? 'success' : 'error'} sx={{ height: 6, borderRadius: 3 }} />
              </Box>
              <Typography variant="caption" className="mono">{f.correlation > 0 ? '+' : ''}{f.correlation.toFixed(2)} (n={f.n})</Typography>
            </>
          )}
        </Stack>
      ))}
    </Stack>
  );
}
