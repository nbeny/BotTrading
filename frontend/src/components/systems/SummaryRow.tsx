'use client';

import { Box, Card, CardContent, Stack, Typography } from '@mui/material';
import BoltIcon from '@mui/icons-material/Bolt';
import CheckCircleIcon from '@mui/icons-material/CheckCircle';
import SyncAltIcon from '@mui/icons-material/SyncAlt';
import PaidIcon from '@mui/icons-material/Paid';
import StorageIcon from '@mui/icons-material/Storage';
import type { SystemsSummary } from '@/lib/types/systems';

function Tile({
  label,
  value,
  sub,
  icon,
  accent,
  delay,
}: {
  label: string;
  value: React.ReactNode;
  sub?: React.ReactNode;
  icon: React.ReactNode;
  accent: string;
  delay: number;
}) {
  return (
    <Card className="reveal" sx={{ ['--d' as string]: `${delay}ms`, position: 'relative', overflow: 'hidden' }}>
      <Box
        sx={{
          position: 'absolute',
          inset: 0,
          background: `radial-gradient(120px 80px at 100% 0%, ${accent}22, transparent 70%)`,
          pointerEvents: 'none',
        }}
      />
      <CardContent sx={{ position: 'relative' }}>
        <Stack direction="row" justifyContent="space-between" alignItems="flex-start">
          <Typography variant="caption" color="text.secondary" sx={{ textTransform: 'uppercase', letterSpacing: 0.7 }}>
            {label}
          </Typography>
          <Box sx={{ color: accent, display: 'grid', placeItems: 'center' }}>{icon}</Box>
        </Stack>
        <Typography className="mono" sx={{ mt: 1, fontWeight: 800, fontSize: 26, lineHeight: 1.1 }}>
          {value}
        </Typography>
        <Box sx={{ mt: 0.5, minHeight: 20 }}>{sub}</Box>
      </CardContent>
    </Card>
  );
}

export function SummaryRow({ s }: { s?: SystemsSummary }) {
  const fmt = (n?: number) => (n == null ? '—' : n.toLocaleString('fr-FR'));
  const health = s ? `${s.services_healthy}/${s.services_total}` : '—';
  return (
    <Box
      sx={{
        display: 'grid',
        gap: 2,
        gridTemplateColumns: { xs: '1fr 1fr', md: 'repeat(5, 1fr)' },
      }}
    >
      <Tile
        label="Services actifs"
        value={health}
        accent="#26d07c"
        icon={<CheckCircleIcon />}
        delay={0}
        sub={
          s && (
            <Typography variant="caption" sx={{ color: s.services_degraded ? '#ffb547' : 'text.secondary' }}>
              {s.services_degraded > 0 ? `${s.services_degraded} dégradé(s)` : 'tous opérationnels'}
              {s.services_down > 0 ? ` · ${s.services_down} hors ligne` : ''}
            </Typography>
          )
        }
      />
      <Tile
        label="Événements / min"
        value={fmt(s?.events_per_min)}
        accent="#4d9fff"
        icon={<BoltIcon />}
        delay={60}
        sub={<Typography variant="caption" color="text.secondary">bus Kafka temps réel</Typography>}
      />
      <Tile
        label="Lag Kafka"
        value={fmt(s?.kafka_lag_total)}
        accent={s && s.kafka_lag_total > 400 ? '#ffb547' : '#22d3ee'}
        icon={<SyncAltIcon />}
        delay={120}
        sub={<Typography variant="caption" color="text.secondary">messages en attente</Typography>}
      />
      <Tile
        label="Coût IA (jour)"
        value={s ? `$${s.ai_cost_today_usd.toFixed(2)}` : '—'}
        accent="#a78bfa"
        icon={<PaidIcon />}
        delay={180}
        sub={<Typography variant="caption" color="text.secondary">Haiku + Sonnet</Typography>}
      />
      <Tile
        label="Uptime global"
        value={s ? `${s.global_uptime_pct.toFixed(2)}%` : '—'}
        accent="#26d07c"
        icon={<StorageIcon />}
        delay={240}
        sub={<Typography variant="caption" color="text.secondary">{fmt(s?.data_points_today)} points / jour</Typography>}
      />
    </Box>
  );
}
