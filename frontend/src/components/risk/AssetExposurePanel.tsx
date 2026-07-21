'use client';

import {
  Box,
  Card,
  CardContent,
  Chip,
  Skeleton,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  Typography,
} from '@mui/material';
import ShieldIcon from '@mui/icons-material/Shield';
import WarningIcon from '@mui/icons-material/Warning';
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip as ReTooltip,
  ReferenceLine,
  ResponsiveContainer,
  Cell,
  Legend,
} from 'recharts';
import { fmtUsd, fmtPct } from '@/lib/format';
import { EmptyState } from '@/components/common';
import type { AssetExposure } from '@/lib/types/domain';

interface Props {
  assets: AssetExposure[] | undefined;
  isLoading: boolean;
}

const TOOLTIP_STYLE = {
  contentStyle: {
    background: '#121722',
    border: '1px solid rgba(255,255,255,0.1)',
    borderRadius: 12,
    fontSize: 12,
  },
};

export function AssetExposurePanel({ assets, isLoading }: Props) {
  if (isLoading) {
    return (
      <Card>
        <CardContent>
          <Skeleton variant="text" width={200} height={28} />
          <Skeleton variant="rectangular" height={200} sx={{ mt: 2, borderRadius: 1 }} />
        </CardContent>
      </Card>
    );
  }

  if (!assets || assets.length === 0) {
    return (
      <Card>
        <CardContent>
          <Typography variant="subtitle1" fontWeight={700} gutterBottom>
            Exposition par actif
          </Typography>
          <EmptyState message="Aucune exposition par actif disponible." />
        </CardContent>
      </Card>
    );
  }

  const chartData = assets.map((a) => ({
    symbol: a.symbol,
    exposition: parseFloat(a.exposure_pct.toFixed(2)),
    limite: parseFloat(a.limit_pct.toFixed(2)),
    over: a.exposure_pct > a.limit_pct,
  }));

  return (
    <Card>
      <CardContent>
        <Typography variant="subtitle1" fontWeight={700} gutterBottom>
          Exposition par actif
        </Typography>

        {/* Bar Chart */}
        <Box sx={{ mt: 2, height: 220 }}>
          <ResponsiveContainer width="100%" height="100%">
            <BarChart data={chartData} margin={{ top: 4, right: 16, left: -8, bottom: 0 }}>
              <XAxis dataKey="symbol" tick={{ fontSize: 11, fill: '#aaa' }} />
              <YAxis tick={{ fontSize: 11, fill: '#aaa' }} unit="%" />
              <ReTooltip
                {...TOOLTIP_STYLE}
                formatter={(val: number, name: string) => [
                  `${val}%`,
                  name === 'exposition' ? 'Exposition' : 'Limite',
                ]}
              />
              <Legend
                formatter={(val) => (val === 'exposition' ? 'Exposition' : 'Limite')}
              />
              <Bar dataKey="exposition" name="exposition" radius={[3, 3, 0, 0]}>
                {chartData.map((entry, i) => (
                  <Cell
                    key={i}
                    fill={entry.over ? '#ef4444' : '#5b8def'}
                  />
                ))}
              </Bar>
              <Bar dataKey="limite" name="limite" fill="rgba(251,191,36,0.4)" radius={[3, 3, 0, 0]} />
            </BarChart>
          </ResponsiveContainer>
        </Box>

        {/* Table */}
        <Box sx={{ mt: 3, overflowX: 'auto' }}>
          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell>Actif</TableCell>
                <TableCell align="right">Exposition $</TableCell>
                <TableCell align="right">Exposition %</TableCell>
                <TableCell align="right">Limite %</TableCell>
                <TableCell align="center">Protégé</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {assets.map((asset) => {
                const over = asset.exposure_pct > asset.limit_pct;
                return (
                  <TableRow
                    key={asset.symbol}
                    sx={over ? { background: 'rgba(239,68,68,0.08)' } : undefined}
                  >
                    <TableCell>
                      <Box sx={{ display: 'flex', alignItems: 'center', gap: 1 }}>
                        <Typography variant="body2" className="mono" fontWeight={600}>
                          {asset.symbol}
                        </Typography>
                        {over && (
                          <WarningIcon sx={{ fontSize: 14, color: 'error.main' }} />
                        )}
                      </Box>
                    </TableCell>
                    <TableCell align="right" className="mono">
                      {fmtUsd(asset.exposure_usd)}
                    </TableCell>
                    <TableCell
                      align="right"
                      className="mono"
                      sx={over ? { color: 'error.main', fontWeight: 700 } : undefined}
                    >
                      {fmtPct(asset.exposure_pct)}
                    </TableCell>
                    <TableCell align="right" className="mono">
                      {fmtPct(asset.limit_pct)}
                    </TableCell>
                    <TableCell align="center">
                      {asset.protected ? (
                        <ShieldIcon sx={{ fontSize: 16, color: 'success.main' }} />
                      ) : (
                        <Typography variant="caption" color="text.disabled">—</Typography>
                      )}
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        </Box>
      </CardContent>
    </Card>
  );
}
