'use client';

import { Box } from '@mui/material';
import { PageHeader } from '@/components/common';
import { SessionPanel } from '@/components/settings/SessionPanel';
import { RbacMatrixPanel } from '@/components/settings/RbacMatrixPanel';
import { ConnectionPanel } from '@/components/settings/ConnectionPanel';
import { PreferencesPanel } from '@/components/settings/PreferencesPanel';
import { SourcesPanel } from '@/components/settings/SourcesPanel';
import { CoveragePanel } from '@/components/settings/CoveragePanel';

export default function SettingsPage() {
  return (
    <Box sx={{ p: { xs: 2, md: 3 } }}>
      <PageHeader
        title="Paramètres"
        subtitle="Session utilisateur, droits d'accès, connexion et préférences"
      />

      <Box
        sx={{
          display: 'grid',
          gap: 3,
          gridTemplateColumns: { xs: '1fr', lg: '1fr 1fr' },
        }}
      >
        {/* Session */}
        <SessionPanel />

        {/* Connection */}
        <ConnectionPanel />

        {/* Sources — full width */}
        <Box sx={{ gridColumn: { xs: '1', lg: '1 / -1' } }}>
          <SourcesPanel />
        </Box>

        {/* Coverage & backfill */}
        <CoveragePanel />

        {/* RBAC Matrix — full width */}
        <Box sx={{ gridColumn: { xs: '1', lg: '1 / -1' } }}>
          <RbacMatrixPanel />
        </Box>

        {/* Preferences — full width */}
        <Box sx={{ gridColumn: { xs: '1', lg: '1 / -1' } }}>
          <PreferencesPanel />
        </Box>
      </Box>
    </Box>
  );
}
