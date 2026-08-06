'use client';

import { Suspense, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Box, Stack, ToggleButton, ToggleButtonGroup, Typography } from '@mui/material';
import { PageHeader } from '@/components/common';
import { SectionCard } from '@/components/systems/common';
import { JournalTable } from '@/components/journal/JournalTable';
import { CalibrationPanel } from '@/components/journal/CalibrationPanel';
import { AttributionPanel } from '@/components/journal/AttributionPanel';
import { journalApi } from '@/lib/api/endpoints';
import { useDecisionParam } from '@/lib/hooks/useDecisionParam';
import type { JournalWindow } from '@/lib/types/journal';

function JournalContent() {
  const [window, setWindow] = useState<JournalWindow>('30d');
  const { open } = useDecisionParam();
  const decisions = useQuery({
    queryKey: ['journal', 'decisions', window],
    queryFn: () => journalApi.decisions(window),
    refetchInterval: 60_000,
  });

  return (
    <Box sx={{ p: { xs: 2, md: 3 } }}>
      <PageHeader
        title="Journal contrefactuel"
        subtitle="L'edge fonctionne-t-il ? Décisions jugées, calibration de seuil, attribution"
        actions={
          <ToggleButtonGroup size="small" exclusive value={window} onChange={(_, v) => v && setWindow(v)}>
            <ToggleButton value="7d">7j</ToggleButton>
            <ToggleButton value="30d">30j</ToggleButton>
            <ToggleButton value="90d">90j</ToggleButton>
          </ToggleButtonGroup>
        }
      />
      <Box sx={{ display: 'grid', gap: 2, gridTemplateColumns: { xs: '1fr', lg: '2fr 1fr' }, alignItems: 'start' }}>
        <SectionCard title="Décisions jugées" subtitle={`${decisions.data?.total ?? '—'} sur la fenêtre · horizon ${decisions.data?.horizon ?? '—'}`}>
          <JournalTable rows={decisions.data?.rows ?? []} loading={decisions.isLoading} onSelect={open} />
        </SectionCard>
        <Stack spacing={2}>
          <SectionCard title="Calibration de seuil"><CalibrationPanel window={window} /></SectionCard>
          <SectionCard title="Attribution"><AttributionPanel window={window} /></SectionCard>
        </Stack>
      </Box>
      {decisions.data?.current_threshold !== null && decisions.data?.current_threshold !== undefined && (
        <Typography variant="caption" sx={{ opacity: 0.6, mt: 1, display: 'block' }}>
          Seuil actuellement appliqué par risk-engine : {decisions.data.current_threshold}
        </Typography>
      )}
    </Box>
  );
}

export default function JournalPage() {
  return (
    <Suspense fallback={null}>
      <JournalContent />
    </Suspense>
  );
}
