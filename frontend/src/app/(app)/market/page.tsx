'use client';

import { Suspense, useCallback, useEffect, useState } from 'react';
import { useRouter, useSearchParams } from 'next/navigation';
import { Box, Card, CardContent, Typography } from '@mui/material';
import { useQuery } from '@tanstack/react-query';
import { marketApi } from '@/lib/api/endpoints';
import { PageHeader } from '@/components/common';
import { TokensTable } from '@/components/market/TokensTable';
import { WorkerDecisionsPanel } from '@/components/market/WorkerDecisionsPanel';
import { NewsPanel } from '@/components/market/NewsPanel';
import { TokenDossierDrawer } from '@/components/market/TokenDossierDrawer';

/** Hauteur des deux colonnes de flux : elles scrollent en interne pour que la
 *  page garde une hauteur constante, quel que soit le volume de contenu. */
const FEED_HEIGHT = 420;

/**
 * Corps de la page. Séparé de `MarketPage` parce que `useSearchParams` exige
 * une frontière `Suspense` en App Router (Next 15) — sans elle, le build
 * statique échoue sur cette route. Voir la frontière posée par `MarketPage`
 * ci-dessous.
 */
function MarketPageContent() {
  const router = useRouter();
  const params = useSearchParams();
  // La sélection vit dans l'URL : le dossier devient partageable, et le bouton
  // retour du navigateur le referme au lieu de quitter la page.
  const selectedSymbol = params.get('token');

  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 30_000);
    return () => clearInterval(id);
  }, []);

  const { data: tokens = [], isLoading: tokensLoading } = useQuery({
    queryKey: ['market', 'tokens'],
    queryFn: marketApi.tokens,
    refetchInterval: 30_000,
  });

  const { data: news = [], isLoading: newsLoading } = useQuery({
    queryKey: ['market', 'news'],
    queryFn: () => marketApi.news(20),
    refetchInterval: 60_000,
  });

  const { data: decisions = [], isLoading: decisionsLoading } = useQuery({
    queryKey: ['market', 'decisions'],
    queryFn: () => marketApi.decisions(30),
    refetchInterval: 30_000,
  });

  const select = useCallback(
    (symbol: string) => router.push(`/market?token=${symbol}`, { scroll: false }),
    [router],
  );
  const close = useCallback(() => router.push('/market', { scroll: false }), [router]);

  const selectedToken = tokens.find((t) => t.symbol === selectedSymbol) ?? null;

  return (
    <Box>
      <PageHeader
        title="Intelligence de marché"
        subtitle="Balayez le marché, cliquez un token pour ouvrir son dossier complet"
      />

      <Card sx={{ mb: 3 }}>
        <CardContent>
          <Typography variant="h6" sx={{ mb: 2 }}>
            Tokens surveillés
          </Typography>
          <TokensTable
            tokens={tokens}
            loading={tokensLoading}
            selectedSymbol={selectedSymbol}
            onSelect={select}
          />
        </CardContent>
      </Card>

      {/* Flux globaux. Le contenu filtré par token vit dans le drawer — le
          garder aussi ici ferait deux chemins pour la même donnée. */}
      <Box
        sx={{
          display: 'grid',
          gap: 3,
          gridTemplateColumns: { xs: '1fr', lg: '1fr 1fr' },
          alignItems: 'start',
        }}
      >
        <Box sx={{ maxHeight: FEED_HEIGHT, overflowY: 'auto' }}>
          <WorkerDecisionsPanel decisions={decisions} loading={decisionsLoading} now={now} />
        </Box>
        <Box sx={{ maxHeight: FEED_HEIGHT, overflowY: 'auto' }}>
          <NewsPanel news={news} loading={newsLoading} now={now} />
        </Box>
      </Box>

      <TokenDossierDrawer token={selectedToken} onClose={close} now={now} />
    </Box>
  );
}

export default function MarketPage() {
  return (
    <Suspense fallback={null}>
      <MarketPageContent />
    </Suspense>
  );
}
