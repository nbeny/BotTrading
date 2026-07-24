'use client';
import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Box } from '@mui/material';
import { PageHeader } from '@/components/common';
import { dataApi } from '@/lib/api/endpoints';
import { DataStatsRow } from '@/components/data/DataStatsRow';
import { IngestionVolumeChart } from '@/components/data/IngestionVolumeChart';
import { SentimentTrendChart } from '@/components/data/SentimentTrendChart';
import { TopSourcesChart } from '@/components/data/TopSourcesChart';
import { MentionsChart } from '@/components/data/MentionsChart';
import { DataFilterBar } from '@/components/data/DataFilterBar';
import { ContentFirehoseTable } from '@/components/data/ContentFirehoseTable';
import { ContentDetailDrawer } from '@/components/data/ContentDetailDrawer';
import { DecisionTraceDrawer } from '@/components/command/DecisionTraceDrawer';
import { SectionCard } from '@/components/systems/common';
import type { ContentQuery, RawContentItem } from '@/lib/types/content';

export default function DataExplorerPage() {
  const stats = useQuery({ queryKey: ['data', 'stats'], queryFn: dataApi.stats, refetchInterval: 15000 });
  const [filters, setFilters] = useState<ContentQuery>({ category: 'all', sentiment: 'all', limit: 60 });
  const [detail, setDetail] = useState<RawContentItem | null>(null);
  const [traceCid, setTraceCid] = useState<string | null>(null);
  const content = useQuery({ queryKey: ['data', 'content', filters], queryFn: () => dataApi.content(filters), refetchInterval: 10000 });
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
      <Box sx={{ mt: 2 }}>
        <SectionCard title="Firehose — contenu collecté" subtitle={`${content.data?.total ?? 0} items`} accent="#a78bfa" noPad
          actions={<Box sx={{ display: { xs: 'none', md: 'block' } }}><DataFilterBar value={filters} onChange={setFilters} /></Box>}>
          <Box sx={{ p: 1.5, display: { xs: 'block', md: 'none' } }}><DataFilterBar value={filters} onChange={setFilters} /></Box>
          <Box sx={{ maxHeight: 520, overflowY: 'auto', overflowX: 'auto' }}>
            <Box sx={{ minWidth: 640 }}><ContentFirehoseTable items={content.data?.items ?? []} onSelect={setDetail} /></Box>
          </Box>
        </SectionCard>
      </Box>
      <ContentDetailDrawer item={detail} onClose={() => setDetail(null)} onOpenTrace={(cid) => { setDetail(null); setTraceCid(cid); }} />
      <DecisionTraceDrawer correlationId={traceCid} onClose={() => setTraceCid(null)} />
    </Box>
  );
}
