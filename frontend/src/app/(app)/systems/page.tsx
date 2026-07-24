'use client';

import { useQuery } from '@tanstack/react-query';
import { Box, Chip, CircularProgress, Stack, Typography } from '@mui/material';
import AccountTreeIcon from '@mui/icons-material/AccountTree';
import DnsIcon from '@mui/icons-material/Dns';
import PsychologyIcon from '@mui/icons-material/Psychology';
import PodcastsIcon from '@mui/icons-material/Podcasts';
import RssFeedIcon from '@mui/icons-material/RssFeed';
import LayersIcon from '@mui/icons-material/Layers';

import { systemsApi } from '@/lib/api/endpoints';
import { PageHeader } from '@/components/common';
import { SummaryRow } from '@/components/systems/SummaryRow';
import { PipelineFlow } from '@/components/systems/PipelineFlow';
import { ServiceGrid } from '@/components/systems/ServiceGrid';
import { KafkaPanel } from '@/components/systems/KafkaPanel';
import { CollectorsPanel } from '@/components/systems/CollectorsPanel';
import { AiWorkersPanel } from '@/components/systems/AiWorkersPanel';
import { InfraPanel } from '@/components/systems/InfraPanel';
import { SectionCard } from '@/components/systems/common';

export default function SystemsPage() {
  const { data, isLoading, dataUpdatedAt } = useQuery({
    queryKey: ['systems', 'overview'],
    queryFn: systemsApi.overview,
    refetchInterval: 5_000,
  });

  return (
    <Box sx={{ p: { xs: 2, md: 3 } }}>
      <PageHeader
        title="Systèmes & Pipeline"
        subtitle="Observabilité temps réel de tous les microservices, du bus Kafka et de l'infrastructure"
        actions={
          <Stack direction="row" spacing={1} alignItems="center">
            {isLoading && <CircularProgress size={16} thickness={5} />}
            <Chip
              size="small"
              variant="outlined"
              color="success"
              className="mono"
              label={
                dataUpdatedAt > 0
                  ? `sync ${new Date(dataUpdatedAt).toLocaleTimeString('fr-FR')}`
                  : 'connexion…'
              }
            />
          </Stack>
        }
      />

      <SummaryRow s={data?.summary} />

      <Box sx={{ mt: 2 }}>
        <SectionCard
          title="Pipeline temps réel"
          subtitle="Données → sentiment → triage → analyse → décision → risque → exécution"
          icon={<AccountTreeIcon />}
          accent="#4d9fff"
        >
          {data ? (
            <PipelineFlow stages={data.pipeline} />
          ) : (
            <Box sx={{ height: 96 }} className="shimmer" />
          )}
        </SectionCard>
      </Box>

      {/* Services map + AI/Kafka rail */}
      <Box
        sx={{
          mt: 2,
          display: 'grid',
          gap: 2,
          gridTemplateColumns: { xs: '1fr', lg: '1.55fr 1fr' },
          alignItems: 'start',
        }}
      >
        <SectionCard
          title="Cartographie des services"
          subtitle="État, débit et ressources de chaque microservice"
          icon={<DnsIcon />}
          accent="#22d3ee"
          delay={60}
        >
          {data ? (
            <ServiceGrid services={data.services} />
          ) : (
            <Box sx={{ height: 280 }} className="shimmer" />
          )}
        </SectionCard>

        <Stack spacing={2}>
          <SectionCard
            title="Workers IA"
            subtitle="Claude Haiku (triage) + Sonnet (senior)"
            icon={<PsychologyIcon />}
            accent="#a78bfa"
            delay={120}
          >
            {data ? <AiWorkersPanel workers={data.workers} /> : <Box sx={{ height: 180 }} className="shimmer" />}
          </SectionCard>

          <SectionCard
            title="Topics Kafka"
            subtitle="Débit, lag et consommateurs du bus d'événements"
            icon={<LayersIcon />}
            accent="#4d9fff"
            delay={180}
          >
            {data ? <KafkaPanel topics={data.kafka} /> : <Box sx={{ height: 220 }} className="shimmer" />}
          </SectionCard>
        </Stack>
      </Box>

      {/* Collectors + Infra */}
      <Box
        sx={{
          mt: 2,
          display: 'grid',
          gap: 2,
          gridTemplateColumns: { xs: '1fr', lg: '1.55fr 1fr' },
          alignItems: 'start',
        }}
      >
        <SectionCard
          title="Sources de collecte"
          subtitle="Boucles de polling auto-régulées → Postgres raw_content"
          icon={<PodcastsIcon />}
          accent="#26d07c"
          delay={220}
        >
          {data ? <CollectorsPanel collectors={data.collectors} /> : <Box sx={{ height: 260 }} className="shimmer" />}
        </SectionCard>

        <SectionCard
          title="Infrastructure"
          subtitle="Postgres · Redis · Kafka · Traefik"
          icon={<RssFeedIcon />}
          accent="#22d3ee"
          delay={260}
        >
          {data ? <InfraPanel infra={data.infra} /> : <Box sx={{ height: 260 }} className="shimmer" />}
        </SectionCard>
      </Box>
    </Box>
  );
}
