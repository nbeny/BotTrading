'use client';

import { useState } from 'react';
import {
  Box,
  Chip,
  Divider,
  Drawer,
  IconButton,
  Skeleton,
  Stack,
  Typography,
} from '@mui/material';
import CloseIcon from '@mui/icons-material/Close';
import OpenInFullIcon from '@mui/icons-material/OpenInFull';
import CloseFullscreenIcon from '@mui/icons-material/CloseFullscreen';
import WhatshotIcon from '@mui/icons-material/Whatshot';
import { useQuery } from '@tanstack/react-query';
import { marketApi } from '@/lib/api/endpoints';
import type { MarketToken } from '@/lib/types/domain';
import { DeltaText, EmptyState } from '@/components/common';
import { fmtUsd } from '@/lib/format';
import { TokenPricePanel } from './TokenPricePanel';
import { WorkerDecisionsPanel } from './WorkerDecisionsPanel';
import { NewsPanel } from './NewsPanel';
import { ScoreBreakdown } from './ScoreBreakdown';
import { PipelineVerdictPanel } from './PipelineVerdictPanel';
import { TokenExposurePanel } from './TokenExposurePanel';

interface Props {
  token: MarketToken | null;
  onClose: () => void;
  now: number;
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <Box>
      <Typography
        variant="caption"
        color="text.secondary"
        sx={{ textTransform: 'uppercase', letterSpacing: 0.6, display: 'block', mb: 1 }}
      >
        {title}
      </Typography>
      {children}
    </Box>
  );
}

/**
 * Tout ce que la plateforme sait d'un token, dans un panneau latéral.
 *
 * L'en-tête est rendu depuis le `MarketToken` déjà en mémoire, donc il
 * s'affiche au clic sans attendre le réseau ; seules les sections en aval
 * dépendent de la requête. Sur erreur, on ne rend jamais un dossier vide —
 * ça se lirait comme « ce token n'a aucune donnée » alors que la vérité est
 * « la requête a échoué ».
 */
export function TokenDossierDrawer({ token, onClose, now }: Props) {
  const [wide, setWide] = useState(false);

  const { data, isLoading, isError } = useQuery({
    queryKey: ['market', 'dossier', token?.symbol],
    queryFn: () => marketApi.dossier(token!.symbol),
    enabled: !!token,
    refetchInterval: 60_000,
  });

  return (
    <Drawer
      anchor="right"
      open={!!token}
      onClose={onClose}
      PaperProps={{
        sx: {
          width: { xs: '100%', sm: wide ? 1040 : 640 },
          maxWidth: '100%',
          bgcolor: 'rgba(8,11,20,0.92)',
          backdropFilter: 'blur(16px)',
          p: 2.5,
          transition: 'width 0.2s',
        },
      }}
    >
      {token && (
        <Stack spacing={2.5}>
          {/* En-tête */}
          <Stack direction="row" justifyContent="space-between" alignItems="flex-start">
            <Box>
              <Stack direction="row" spacing={1} alignItems="center">
                <Typography variant="h6" className="mono" sx={{ fontWeight: 800 }}>
                  {token.symbol}
                </Typography>
                <Typography variant="body2" color="text.secondary">
                  {token.name}
                </Typography>
                {token.is_trending && (
                  <Chip
                    icon={<WhatshotIcon />}
                    label="Hot"
                    color="warning"
                    size="small"
                    variant="outlined"
                  />
                )}
              </Stack>
              <Stack direction="row" spacing={1.5} alignItems="baseline">
                <Typography variant="h5" className="mono" sx={{ fontWeight: 800 }}>
                  {fmtUsd(token.price_usd)}
                </Typography>
                <DeltaText value={token.price_change_pct_24h} variant="body2" />
              </Stack>
            </Box>
            <Stack direction="row" spacing={0.5}>
              <IconButton
                size="small"
                onClick={() => setWide((v) => !v)}
                aria-label={wide ? 'Réduire' : 'Élargir'}
                sx={{ display: { xs: 'none', sm: 'inline-flex' } }}
              >
                {wide ? <CloseFullscreenIcon fontSize="small" /> : <OpenInFullIcon fontSize="small" />}
              </IconButton>
              <IconButton size="small" onClick={onClose} aria-label="Fermer">
                <CloseIcon fontSize="small" />
              </IconButton>
            </Stack>
          </Stack>

          <Divider />

          <TokenPricePanel token={token} bare />

          {isError ? (
            <EmptyState message="Le dossier n'a pas pu être chargé." />
          ) : isLoading || !data ? (
            <Stack spacing={2}>
              {[0, 1, 2].map((i) => (
                <Skeleton key={i} variant="rectangular" height={120} sx={{ borderRadius: 2 }} />
              ))}
            </Stack>
          ) : (
            <Stack spacing={2.5} divider={<Divider />}>
              <Section title="Décomposition du score">
                <ScoreBreakdown score={data.score} />
              </Section>

              <Section title="Verdict du pipeline">
                <PipelineVerdictPanel verdict={data.pipeline} />
              </Section>

              <Section title={`Décisions des workers · ${token.symbol}`}>
                {data.decisions.length === 0 ? (
                  <EmptyState message="Aucune décision worker sur ce token." />
                ) : (
                  <WorkerDecisionsPanel
                    decisions={data.decisions}
                    loading={false}
                    now={now}
                    bare
                  />
                )}
              </Section>

              <Section title={`News & social · ${token.symbol}`}>
                {data.content.length === 0 ? (
                  <EmptyState message="Aucune news sur ce token." />
                ) : (
                  <NewsPanel news={data.content} loading={false} now={now} bare />
                )}
              </Section>

              <Section title="Exposition">
                <TokenExposurePanel exposure={data.exposure} />
              </Section>
            </Stack>
          )}
        </Stack>
      )}
    </Drawer>
  );
}
