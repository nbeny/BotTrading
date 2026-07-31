'use client';

import { Box, Chip, Stack, Tooltip, Typography } from '@mui/material';
import { HEALTH_COLOR, HealthDot, MiniBar, Sparkline } from './common';
import {
  GROUP_LABEL,
  HEALTH_LABEL,
  type ServiceGroup,
  type ServiceNode,
} from '@/lib/types/systems';
import { fmtRate } from '@/lib/format';

const GROUP_ORDER: ServiceGroup[] = ['edge', 'collect', 'analyze', 'decide', 'execute'];
const GROUP_ACCENT: Record<ServiceGroup, string> = {
  edge: '#22d3ee',
  collect: '#4d9fff',
  analyze: '#a78bfa',
  decide: '#ffb547',
  execute: '#26d07c',
  infra: '#8b97b0',
};

function Metric({ label, value, color }: { label: string; value: string; color?: string }) {
  return (
    <Stack spacing={0.1} sx={{ minWidth: 0 }}>
      <Typography variant="caption" color="text.secondary" sx={{ fontSize: 9.5, textTransform: 'uppercase', letterSpacing: 0.5 }}>
        {label}
      </Typography>
      <Typography className="mono" sx={{ fontSize: 12.5, fontWeight: 700, color: color ?? 'text.primary' }}>
        {value}
      </Typography>
    </Stack>
  );
}

function ServiceCard({ s, accent, delay }: { s: ServiceNode; accent: string; delay: number }) {
  const color = HEALTH_COLOR[s.status];
  const latColor = s.latency_ms > 250 ? '#ffb547' : s.latency_ms > 400 ? '#ff5370' : undefined;
  return (
    <Box
      className="reveal"
      sx={{
        ['--d' as string]: `${delay}ms`,
        p: 1.75,
        borderRadius: 3,
        border: '1px solid',
        borderColor: 'divider',
        background: 'linear-gradient(180deg, rgba(255,255,255,0.028), rgba(255,255,255,0.006))',
        transition: 'border-color .16s ease, transform .16s ease',
        '&:hover': { borderColor: `${accent}66`, transform: 'translateY(-2px)' },
        display: 'flex',
        flexDirection: 'column',
        gap: 1.25,
      }}
    >
      <Stack direction="row" alignItems="flex-start" justifyContent="space-between" spacing={1}>
        <Stack direction="row" spacing={1} alignItems="center" sx={{ minWidth: 0 }}>
          <HealthDot status={s.status} />
          <Box sx={{ minWidth: 0 }}>
            <Typography sx={{ fontWeight: 700, fontSize: 13.5, lineHeight: 1.15 }} noWrap>
              {s.name}
            </Typography>
            <Typography variant="caption" color="text.secondary" noWrap sx={{ display: 'block', fontSize: 11 }}>
              {s.role}
            </Typography>
          </Box>
        </Stack>
        <Tooltip title={`${HEALTH_LABEL[s.status]} · uptime ${s.uptime_pct}%`}>
          <Chip
            label={s.replicas > 1 ? `×${s.replicas}` : '×1'}
            size="small"
            variant="outlined"
            className="mono"
            sx={{ height: 20, fontSize: 10.5, borderColor: `${color}55`, color }}
          />
        </Tooltip>
      </Stack>

      <Box sx={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 1 }}>
        <Metric label="Débit" value={fmtRate(s.throughput_per_min)} color={accent} />
        <Metric label="Latence" value={`${s.latency_ms}ms`} color={latColor} />
        <Metric label="RAM" value={s.mem_mb >= 1000 ? `${(s.mem_mb / 1000).toFixed(1)}G` : `${s.mem_mb}M`} />
      </Box>

      <Stack direction="row" alignItems="center" spacing={1}>
        <Box sx={{ flex: 1 }}>
          <Stack direction="row" justifyContent="space-between" sx={{ mb: 0.5 }}>
            <Typography variant="caption" color="text.secondary" sx={{ fontSize: 9.5 }}>
              CPU {s.cpu_pct}%
            </Typography>
            <Typography variant="caption" color="text.secondary" className="mono" sx={{ fontSize: 9.5 }}>
              v{s.version}
            </Typography>
          </Stack>
          <MiniBar pct={s.cpu_pct} color={s.cpu_pct > 75 ? '#ff5370' : accent} height={4} />
        </Box>
        <Sparkline data={s.spark} color={accent} width={70} height={22} />
      </Stack>

      {(s.kafka_in.length > 0 || s.kafka_out.length > 0) && (
        <Stack direction="row" spacing={0.5} flexWrap="wrap" useFlexGap sx={{ gap: 0.5 }}>
          {s.kafka_in.slice(0, 2).map((t) => (
            <Chip key={`in-${t}`} label={`in · ${t}`} size="small" sx={{ height: 18, fontSize: 9.5, bgcolor: 'rgba(77,159,255,0.1)', color: '#7fb8ff' }} />
          ))}
          {s.kafka_out.slice(0, 2).map((t) => (
            <Chip key={`out-${t}`} label={`out · ${t}`} size="small" sx={{ height: 18, fontSize: 9.5, bgcolor: 'rgba(38,208,124,0.1)', color: '#4fe0a0' }} />
          ))}
        </Stack>
      )}
    </Box>
  );
}

export function ServiceGrid({ services }: { services: ServiceNode[] }) {
  const groups = GROUP_ORDER.filter((g) => services.some((s) => s.group === g));
  let idx = 0;
  return (
    <Stack spacing={2.5}>
      {groups.map((g) => {
        const items = services.filter((s) => s.group === g);
        const accent = GROUP_ACCENT[g];
        return (
          <Box key={g}>
            <Stack direction="row" alignItems="center" spacing={1} sx={{ mb: 1.25 }}>
              <Box sx={{ width: 3, height: 16, borderRadius: 1, bgcolor: accent, boxShadow: `0 0 8px ${accent}` }} />
              <Typography variant="overline" sx={{ color: 'text.secondary' }}>
                {GROUP_LABEL[g]}
              </Typography>
              <Typography variant="caption" color="text.secondary" className="mono">
                {items.length}
              </Typography>
            </Stack>
            <Box
              sx={{
                display: 'grid',
                gap: 1.5,
                gridTemplateColumns: {
                  xs: '1fr',
                  sm: 'repeat(2, 1fr)',
                  lg: 'repeat(3, 1fr)',
                  xl: 'repeat(4, 1fr)',
                },
              }}
            >
              {items.map((s) => (
                <ServiceCard key={s.id} s={s} accent={accent} delay={(idx++ % 8) * 40} />
              ))}
            </Box>
          </Box>
        );
      })}
    </Stack>
  );
}
