'use client';

import { useState } from 'react';
import { Box, Tab, Tabs } from '@mui/material';
import { PageHeader } from '@/components/common';
import { CapitalTab } from '@/components/capital/CapitalTab';
import { RisqueTab } from '@/components/capital/RisqueTab';

export default function CapitalPage() {
  const [tab, setTab] = useState(0);
  return (
    <Box sx={{ p: { xs: 2, md: 3 } }}>
      <PageHeader title="Capital & Risque" subtitle="Positions, PnL et historique · exposition, limites et alertes" />
      <Tabs value={tab} onChange={(_, v) => setTab(v)} sx={{ mb: 2 }}>
        <Tab label="Capital" />
        <Tab label="Risque" />
      </Tabs>
      <Box hidden={tab !== 0}>{tab === 0 && <CapitalTab />}</Box>
      <Box hidden={tab !== 1}>{tab === 1 && <RisqueTab />}</Box>
    </Box>
  );
}
