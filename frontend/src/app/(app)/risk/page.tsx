'use client';

import { Box, Typography } from '@mui/material';
import { useQuery } from '@tanstack/react-query';
import { PageHeader } from '@/components/common';
import { RiskKpiRow } from '@/components/risk/RiskKpiRow';
import { AssetExposurePanel } from '@/components/risk/AssetExposurePanel';
import { RiskLimitsPanel } from '@/components/risk/RiskLimitsPanel';
import { RiskAlertsPanel } from '@/components/risk/RiskAlertsPanel';
import { riskApi } from '@/lib/api/endpoints';

export default function RiskPage() {
  const { data: exposure, isLoading: loadingExposure } = useQuery({
    queryKey: ['risk', 'exposure'],
    queryFn: riskApi.exposure,
    refetchInterval: 8_000,
  });

  const { data: limits, isLoading: loadingLimits } = useQuery({
    queryKey: ['risk', 'limits'],
    queryFn: riskApi.limits,
    refetchInterval: 8_000,
  });

  const { data: alerts, isLoading: loadingAlerts } = useQuery({
    queryKey: ['risk', 'alerts'],
    queryFn: () => riskApi.alerts(30),
    refetchInterval: 8_000,
  });

  return (
    <Box sx={{ p: { xs: 2, md: 3 } }}>
      <PageHeader
        title="Tableau de bord Risque"
        subtitle="Surveillance en temps réel de l'exposition et des limites de risque"
      />

      {/* KPI Row */}
      <RiskKpiRow data={exposure} isLoading={loadingExposure} />

      {/* Middle section: asset exposure + risk limits */}
      <Box
        sx={{
          display: 'grid',
          gap: 2,
          mt: 3,
          gridTemplateColumns: { xs: '1fr', lg: '3fr 2fr' },
        }}
      >
        <AssetExposurePanel assets={exposure?.by_asset} isLoading={loadingExposure} />
        <RiskLimitsPanel limits={limits} isLoading={loadingLimits} />
      </Box>

      {/* Alerts */}
      <Box sx={{ mt: 3 }}>
        <RiskAlertsPanel alerts={alerts} isLoading={loadingAlerts} />
      </Box>

      {/* Last updated */}
      {exposure?.updated_at && (
        <Typography variant="caption" color="text.disabled" sx={{ display: 'block', mt: 2 }}>
          Dernière mise à jour :{' '}
          {new Date(exposure.updated_at).toLocaleTimeString('fr-FR')}
        </Typography>
      )}
    </Box>
  );
}
