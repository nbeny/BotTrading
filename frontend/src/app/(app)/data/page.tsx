'use client';
import { useQuery } from '@tanstack/react-query';
import { Box } from '@mui/material';
import { PageHeader } from '@/components/common';
import { dataApi } from '@/lib/api/endpoints';
import { DataStatsRow } from '@/components/data/DataStatsRow';
import { IngestionVolumeChart } from '@/components/data/IngestionVolumeChart';
import { SentimentTrendChart } from '@/components/data/SentimentTrendChart';
import { TopSourcesChart } from '@/components/data/TopSourcesChart';
import { MentionsChart } from '@/components/data/MentionsChart';

export default function DataExplorerPage() {
  const stats = useQuery({ queryKey: ['data', 'stats'], queryFn: dataApi.stats, refetchInterval: 15000 });
  return (
    <Box sx={{ p: { xs: 2, md: 3 } }}>
      <PageHeader title="Data Explorer" subtitle="Tout le contenu collecté — news, social & marché — scoré et relié aux décisions" />
      <DataStatsRow s={stats.data} />
      <Box sx={{ mt: 2, display: 'grid', gap: 2, gridTemplateColumns: { xs: '1fr', md: '2fr 1fr' } }}>
        <IngestionVolumeChart s={stats.data} />
        <SentimentTrendChart s={stats.data} />
      </Box>
      <Box sx={{ mt: 2, display: 'grid', gap: 2, gridTemplateColumns: { xs: '1fr', md: '1fr 1fr' } }}>
        <TopSourcesChart s={stats.data} />
        <MentionsChart s={stats.data} />
      </Box>
    </Box>
  );
}
