'use client';

import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Box, Slider, Stack, Typography } from '@mui/material';
import { journalApi } from '@/lib/api/endpoints';
import type { CalibrationBucket, JournalWindow } from '@/lib/types/journal';

function BucketStats({ title, bucket, minN }: { title: string; bucket: CalibrationBucket | null; minN: number }) {
  if (!bucket) return <Typography variant="body2" sx={{ opacity: 0.6 }}>{title} : seuil inconnu de ce conteneur — « — »</Typography>;
  return (
    <Box>
      <Typography variant="caption" sx={{ opacity: 0.6 }}>{title} (seuil {bucket.threshold})</Typography>
      <Typography variant="body2" className="mono">
        {bucket.selected} sél. · {bucket.judged} jugées ·{' '}
        {bucket.sufficient
          ? `win ${Math.round((bucket.win_rate ?? 0) * 100)}% · PnL ${bucket.total_pnl_pct! > 0 ? '+' : ''}${bucket.total_pnl_pct}%`
          : `— (échantillon insuffisant, n < ${minN})`}
      </Typography>
    </Box>
  );
}

export function CalibrationPanel({ window }: { window: JournalWindow }) {
  const [threshold, setThreshold] = useState(70);
  const [applied, setApplied] = useState(70);
  const { data } = useQuery({
    queryKey: ['journal', 'calibration', window, applied],
    queryFn: () => journalApi.calibration(applied, window),
    placeholderData: (prev) => prev,
  });

  return (
    <Stack spacing={1.5}>
      <Slider
        value={threshold}
        min={0}
        max={100}
        onChange={(_, v) => setThreshold(v as number)}
        onChangeCommitted={(_, v) => setApplied(v as number)}
        valueLabelDisplay="auto"
        size="small"
      />
      <BucketStats title="Seuil simulé" bucket={data?.requested ?? null} minN={data?.min_n ?? 20} />
      <BucketStats title="Seuil actuel" bucket={data?.current ?? null} minN={data?.min_n ?? 20} />
    </Stack>
  );
}
