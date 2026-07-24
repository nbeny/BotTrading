'use client';
import { Box, Card, CardContent, Typography } from '@mui/material';
import type { DataStats } from '@/lib/types/content';

function Tile({ label, value, accent }: { label: string; value: React.ReactNode; accent: string }) {
  return (
    <Card className="reveal"><CardContent>
      <Typography className="mono" sx={{ fontWeight: 800, fontSize: 22, color: accent }}>{value}</Typography>
      <Typography variant="caption" color="text.secondary" sx={{ textTransform: 'uppercase', letterSpacing: 0.6 }}>{label}</Typography>
    </CardContent></Card>
  );
}
export function DataStatsRow({ s }: { s?: DataStats }) {
  const f = (n?: number) => (n == null ? '—' : n.toLocaleString('fr-FR'));
  return (
    <Box sx={{ display: 'grid', gap: 2, gridTemplateColumns: { xs: '1fr 1fr', md: 'repeat(5,1fr)' } }}>
      <Tile label="Items / 24h" value={f(s?.total_24h)} accent="#e8ecf5" />
      <Tile label="Social" value={f(s?.social_24h)} accent="#a78bfa" />
      <Tile label="News" value={f(s?.news_24h)} accent="#4d9fff" />
      <Tile label="Marché" value={f(s?.market_24h)} accent="#22d3ee" />
      <Tile label="Sentiment moyen" value={s ? s.avg_sentiment.toFixed(2) : '—'} accent={s && s.avg_sentiment >= 0 ? '#26d07c' : '#ff5370'} />
    </Box>
  );
}
