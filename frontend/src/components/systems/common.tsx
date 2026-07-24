'use client';

import { Box, Card, CardContent, Stack, Typography } from '@mui/material';
import type { ServiceHealth } from '@/lib/types/systems';

export const HEALTH_COLOR: Record<ServiceHealth, string> = {
  healthy: '#26d07c',
  degraded: '#ffb547',
  down: '#ff5370',
  idle: '#5b6579',
};

/** Pulsing status beacon. */
export function HealthDot({ status, size = 9 }: { status: ServiceHealth; size?: number }) {
  const color = HEALTH_COLOR[status];
  return (
    <Box
      className={status === 'healthy' || status === 'degraded' ? 'beacon' : undefined}
      sx={{
        width: size,
        height: size,
        borderRadius: '50%',
        bgcolor: color,
        color,
        flexShrink: 0,
        boxShadow: `0 0 8px ${color}`,
      }}
    />
  );
}

/** Tiny inline SVG sparkline. */
export function Sparkline({
  data,
  color = '#4d9fff',
  width = 96,
  height = 28,
}: {
  data: number[];
  color?: string;
  width?: number;
  height?: number;
}) {
  if (!data.length) return null;
  const min = Math.min(...data);
  const max = Math.max(...data);
  const span = max - min || 1;
  const step = width / (data.length - 1);
  const pts = data.map((v, i) => `${i * step},${height - ((v - min) / span) * (height - 4) - 2}`);
  const d = `M ${pts.join(' L ')}`;
  const area = `${d} L ${width},${height} L 0,${height} Z`;
  const gid = `spark-${color.replace('#', '')}`;
  return (
    <svg width={width} height={height} style={{ display: 'block' }} aria-hidden>
      <defs>
        <linearGradient id={gid} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor={color} stopOpacity={0.32} />
          <stop offset="100%" stopColor={color} stopOpacity={0} />
        </linearGradient>
      </defs>
      <path d={area} fill={`url(#${gid})`} />
      <path d={d} fill="none" stroke={color} strokeWidth={1.6} strokeLinejoin="round" strokeLinecap="round" />
    </svg>
  );
}

/** Titled section wrapper with an accent rail and optional actions. */
export function SectionCard({
  title,
  subtitle,
  icon,
  accent = '#4d9fff',
  actions,
  children,
  delay = 0,
  noPad = false,
}: {
  title: string;
  subtitle?: string;
  icon?: React.ReactNode;
  accent?: string;
  actions?: React.ReactNode;
  children: React.ReactNode;
  delay?: number;
  noPad?: boolean;
}) {
  return (
    <Card className="reveal" sx={{ height: '100%', ['--d' as string]: `${delay}ms` }}>
      <Box
        sx={{
          px: 2.5,
          py: 1.75,
          borderBottom: '1px solid',
          borderColor: 'divider',
          display: 'flex',
          alignItems: 'center',
          gap: 1.5,
        }}
      >
        {icon && (
          <Box
            sx={{
              width: 34,
              height: 34,
              borderRadius: 2,
              display: 'grid',
              placeItems: 'center',
              color: accent,
              bgcolor: `${accent}1f`,
              border: `1px solid ${accent}33`,
            }}
          >
            {icon}
          </Box>
        )}
        <Box sx={{ flex: 1, minWidth: 0 }}>
          <Typography variant="subtitle2" sx={{ fontWeight: 700 }}>
            {title}
          </Typography>
          {subtitle && (
            <Typography variant="caption" color="text.secondary" noWrap sx={{ display: 'block' }}>
              {subtitle}
            </Typography>
          )}
        </Box>
        {actions}
      </Box>
      {noPad ? (
        <Box>{children}</Box>
      ) : (
        <CardContent sx={{ p: 2.5 }}>{children}</CardContent>
      )}
    </Card>
  );
}

export function MiniBar({ pct, color = '#4d9fff', height = 5 }: { pct: number; color?: string; height?: number }) {
  const v = Math.max(0, Math.min(100, pct));
  return (
    <Box sx={{ width: '100%', height, borderRadius: 999, bgcolor: 'rgba(255,255,255,0.06)', overflow: 'hidden' }}>
      <Box sx={{ width: `${v}%`, height: '100%', borderRadius: 999, bgcolor: color, transition: 'width .5s ease' }} />
    </Box>
  );
}

export function Stat({ label, value, hint }: { label: string; value: React.ReactNode; hint?: string }) {
  return (
    <Stack spacing={0.25}>
      <Typography variant="caption" color="text.secondary" sx={{ textTransform: 'uppercase', letterSpacing: 0.6, fontSize: 10 }}>
        {label}
      </Typography>
      <Typography className="mono" sx={{ fontWeight: 700, fontSize: 18, lineHeight: 1.1 }}>
        {value}
      </Typography>
      {hint && (
        <Typography variant="caption" color="text.secondary">
          {hint}
        </Typography>
      )}
    </Stack>
  );
}
