'use client';

import { useMemo, useState } from 'react';
import {
  Box,
  Card,
  CardContent,
  Chip,
  Skeleton,
  Stack,
  ToggleButton,
  ToggleButtonGroup,
  Typography,
} from '@mui/material';
import { DataGrid, type GridColDef } from '@mui/x-data-grid';
import { EmptyState } from '@/components/common';
import { fmtUsd, fmtNum, fmtDateTime } from '@/lib/format';
import { pnlColor } from '@/theme/theme';
import type { Trade, TradingMode } from '@/lib/types/domain';

type ModeFilter = TradingMode | 'all';

interface Props {
  trades: Trade[] | undefined;
  isLoading: boolean;
}

function StatusChip({ status }: { status: Trade['status'] }) {
  const map: Record<Trade['status'], { label: string; color: 'success' | 'warning' | 'error' | 'default' }> = {
    filled: { label: 'Exécuté', color: 'success' },
    partial: { label: 'Partiel', color: 'warning' },
    rejected: { label: 'Rejeté', color: 'error' },
    pending: { label: 'En attente', color: 'default' },
  };
  const c = map[status] ?? { label: status, color: 'default' as const };
  return <Chip label={c.label} color={c.color} size="small" variant="outlined" />;
}

export function TradesTable({ trades, isLoading }: Props) {
  const [modeFilter, setModeFilter] = useState<ModeFilter>('all');

  const filtered = useMemo(() => {
    if (!trades) return [];
    if (modeFilter === 'all') return trades;
    return trades.filter((t) => t.mode === modeFilter);
  }, [trades, modeFilter]);

  const columns = useMemo<GridColDef<Trade>[]>(() => [
    {
      field: 'symbol',
      headerName: 'Symbole',
      width: 110,
      renderCell: ({ value }) => (
        <Typography variant="body2" className="mono" sx={{ fontWeight: 700 }}>
          {value}
        </Typography>
      ),
    },
    {
      field: 'side',
      headerName: 'Côté',
      width: 80,
      renderCell: ({ value }) => (
        <Chip
          label={value === 'buy' ? 'ACHAT' : 'VENTE'}
          color={value === 'buy' ? 'success' : 'error'}
          size="small"
          variant="outlined"
        />
      ),
    },
    {
      field: 'order_type',
      headerName: 'Type',
      width: 90,
      renderCell: ({ value }) => (
        <Typography variant="caption" color="text.secondary" sx={{ textTransform: 'uppercase' }}>
          {value === 'market' ? 'Marché' : 'Limite'}
        </Typography>
      ),
    },
    {
      field: 'price',
      headerName: 'Prix',
      width: 110,
      align: 'right',
      headerAlign: 'right',
      renderCell: ({ value }) => <span className="mono">{fmtUsd(value)}</span>,
    },
    {
      field: 'quantity',
      headerName: 'Qté',
      width: 100,
      align: 'right',
      headerAlign: 'right',
      renderCell: ({ value }) => <span className="mono">{fmtNum(value, 4)}</span>,
    },
    {
      field: 'cost_usd',
      headerName: 'Coût',
      width: 110,
      align: 'right',
      headerAlign: 'right',
      renderCell: ({ value }) => <span className="mono">{fmtUsd(value)}</span>,
    },
    {
      field: 'fee_usd',
      headerName: 'Frais',
      width: 90,
      align: 'right',
      headerAlign: 'right',
      renderCell: ({ value }) => (
        <span className="mono" style={{ color: '#94a0b8' }}>
          {fmtUsd(value)}
        </span>
      ),
    },
    {
      field: 'pnl_usd',
      headerName: 'PnL',
      width: 110,
      align: 'right',
      headerAlign: 'right',
      renderCell: ({ value }) =>
        value != null ? (
          <Typography variant="body2" className="mono" sx={{ color: pnlColor(value), fontWeight: 700 }}>
            {fmtUsd(value)}
          </Typography>
        ) : (
          <span style={{ color: '#94a0b8' }}>—</span>
        ),
    },
    {
      field: 'status',
      headerName: 'Statut',
      width: 110,
      renderCell: ({ value }) => <StatusChip status={value} />,
    },
    {
      field: 'executed_at',
      headerName: 'Exécuté le',
      width: 130,
      renderCell: ({ value }) => (
        <Typography variant="caption" color="text.secondary" className="mono">
          {fmtDateTime(value)}
        </Typography>
      ),
    },
  ], []);

  return (
    <Card>
      <CardContent>
        <Stack direction="row" justifyContent="space-between" alignItems="center" sx={{ mb: 2 }}>
          <Typography
            variant="subtitle2"
            color="text.secondary"
            sx={{ textTransform: 'uppercase', letterSpacing: 0.6 }}
          >
            Historique des trades
          </Typography>
          <ToggleButtonGroup
            size="small"
            exclusive
            value={modeFilter}
            onChange={(_, v) => v && setModeFilter(v as ModeFilter)}
          >
            <ToggleButton value="all" sx={{ px: 1.5, py: 0.25, fontSize: 11 }}>Tous</ToggleButton>
            <ToggleButton value="paper" sx={{ px: 1.5, py: 0.25, fontSize: 11 }}>Paper</ToggleButton>
            <ToggleButton value="live" sx={{ px: 1.5, py: 0.25, fontSize: 11 }}>Live</ToggleButton>
          </ToggleButtonGroup>
        </Stack>

        {isLoading ? (
          <Skeleton variant="rectangular" height={240} sx={{ borderRadius: 1 }} />
        ) : !filtered || filtered.length === 0 ? (
          <EmptyState message="Aucun trade dans l'historique" />
        ) : (
          <Box>
            <DataGrid
              rows={filtered}
              columns={columns}
              getRowId={(row) => row.trade_id}
              autoHeight
              density="compact"
              disableRowSelectionOnClick
              pageSizeOptions={[10, 25, 50]}
              initialState={{ pagination: { paginationModel: { pageSize: 10 } } }}
              sx={{
                border: 'none',
                '& .MuiDataGrid-columnHeaders': { background: 'transparent' },
                '& .MuiDataGrid-cell': { borderColor: 'rgba(255,255,255,0.06)' },
                '& .MuiDataGrid-row:hover': { background: 'rgba(255,255,255,0.03)' },
              }}
            />
          </Box>
        )}
      </CardContent>
    </Card>
  );
}
