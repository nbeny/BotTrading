'use client';

import { Box, Card, CardContent, Skeleton, Stack, Typography } from '@mui/material';
import { Cell, Legend, PieChart, Pie, Tooltip, ResponsiveContainer } from 'recharts';
import { EmptyState } from '@/components/common';
import { fmtUsdCompact, fmtPct } from '@/lib/format';
import type { Position } from '@/lib/types/domain';

const COLORS = [
  '#5b8def', '#26d07c', '#ff5370', '#ffb547', '#8b5cf6',
  '#06b6d4', '#f97316', '#ec4899', '#a3e635', '#fb7185',
];

interface Props {
  positions: Position[] | undefined;
  isLoading: boolean;
}

interface SliceEntry {
  name: string;
  value: number;
  pct: number;
}

export function AllocationDonut({ positions, isLoading }: Props) {
  const total = positions?.reduce((acc, p) => acc + p.value_usd, 0) ?? 0;

  const slices: SliceEntry[] = (positions ?? []).map((p) => ({
    name: p.symbol,
    value: p.value_usd,
    pct: total > 0 ? (p.value_usd / total) * 100 : 0,
  }));

  return (
    <Card sx={{ height: '100%' }}>
      <CardContent>
        <Typography
          variant="subtitle2"
          color="text.secondary"
          sx={{ mb: 2, textTransform: 'uppercase', letterSpacing: 0.6 }}
        >
          Allocation
        </Typography>

        {isLoading ? (
          <Stack spacing={1} alignItems="center">
            <Skeleton variant="circular" width={180} height={180} />
            <Skeleton width="80%" height={16} />
            <Skeleton width="60%" height={16} />
          </Stack>
        ) : !positions || positions.length === 0 ? (
          <EmptyState message="Aucune position ouverte" />
        ) : (
          <Box sx={{ width: '100%', height: 280 }}>
            <ResponsiveContainer width="100%" height="100%">
              <PieChart>
                <Pie
                  data={slices}
                  cx="50%"
                  cy="45%"
                  innerRadius={55}
                  outerRadius={90}
                  paddingAngle={2}
                  dataKey="value"
                  isAnimationActive={false}
                >
                  {slices.map((_, i) => (
                    <Cell key={i} fill={COLORS[i % COLORS.length]} />
                  ))}
                </Pie>
                <Tooltip
                  contentStyle={{
                    background: '#121722',
                    border: '1px solid rgba(255,255,255,0.1)',
                    borderRadius: 12,
                    fontSize: 12,
                  }}
                  formatter={(value: number, name: string) => [
                    `${fmtUsdCompact(value)} (${fmtPct(slices.find((s) => s.name === name)?.pct ?? 0, 1)})`,
                    name,
                  ]}
                />
                <Legend
                  iconType="circle"
                  iconSize={8}
                  formatter={(value: string) => {
                    const s = slices.find((sl) => sl.name === value);
                    return (
                      <span style={{ color: '#e6e9ef', fontSize: 11 }}>
                        {value}{' '}
                        <span style={{ color: '#94a0b8' }}>
                          {s ? fmtPct(s.pct, 1) : ''}
                        </span>
                      </span>
                    );
                  }}
                />
              </PieChart>
            </ResponsiveContainer>
          </Box>
        )}
      </CardContent>
    </Card>
  );
}
