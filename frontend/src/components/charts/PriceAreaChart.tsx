'use client';

import { Box } from '@mui/material';
import {
  Area,
  AreaChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
  CartesianGrid,
} from 'recharts';
import type { PricePoint } from '@/lib/types/domain';
import { fmtTime, fmtUsdCompact } from '@/lib/format';

/** Reusable area chart for price/value time series. */
export function PriceAreaChart({
  data,
  height = 260,
  color = '#5b8def',
  dataKey = 'price',
  showGrid = true,
}: {
  data: PricePoint[];
  height?: number;
  color?: string;
  dataKey?: 'price' | 'volume';
  showGrid?: boolean;
}) {
  const gid = `grad-${dataKey}-${color.replace('#', '')}`;
  return (
    <Box sx={{ width: '100%', height }}>
      <ResponsiveContainer width="100%" height="100%">
        <AreaChart data={data} margin={{ top: 8, right: 8, bottom: 0, left: -8 }}>
          <defs>
            <linearGradient id={gid} x1="0" y1="0" x2="0" y2="1">
              <stop offset="0%" stopColor={color} stopOpacity={0.35} />
              <stop offset="100%" stopColor={color} stopOpacity={0} />
            </linearGradient>
          </defs>
          {showGrid && <CartesianGrid strokeDasharray="3 3" stroke="rgba(255,255,255,0.05)" vertical={false} />}
          <XAxis
            dataKey="t"
            tickFormatter={(t) => fmtTime(t)}
            tick={{ fill: '#94a0b8', fontSize: 11 }}
            axisLine={false}
            tickLine={false}
            minTickGap={40}
          />
          <YAxis
            tickFormatter={(v) => fmtUsdCompact(v)}
            tick={{ fill: '#94a0b8', fontSize: 11 }}
            axisLine={false}
            tickLine={false}
            width={64}
            domain={['auto', 'auto']}
          />
          <Tooltip
            contentStyle={{
              background: '#121722',
              border: '1px solid rgba(255,255,255,0.1)',
              borderRadius: 12,
              fontSize: 12,
            }}
            labelFormatter={(t) => fmtTime(t as string)}
            formatter={(v: number) => [fmtUsdCompact(v), dataKey === 'volume' ? 'Volume' : 'Prix']}
          />
          <Area
            type="monotone"
            dataKey={dataKey}
            stroke={color}
            strokeWidth={2}
            fill={`url(#${gid})`}
            isAnimationActive={false}
          />
        </AreaChart>
      </ResponsiveContainer>
    </Box>
  );
}
