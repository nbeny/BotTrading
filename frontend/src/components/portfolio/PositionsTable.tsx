'use client';

import { useMemo } from 'react';
import { Box, Card, CardContent, Skeleton, Typography } from '@mui/material';
import ShieldIcon from '@mui/icons-material/Shield';
import ShieldOutlinedIcon from '@mui/icons-material/ShieldOutlined';
import { DataGrid, type GridColDef } from '@mui/x-data-grid';
import { DirectionChip, EmptyState } from '@/components/common';
import { fmtUsd, fmtPct, fmtNum, fmtRelative } from '@/lib/format';
import { pnlColor } from '@/theme/theme';
import type { Position } from '@/lib/types/domain';

interface Props {
  positions: Position[] | undefined;
  isLoading: boolean;
}

export function PositionsTable({ positions, isLoading }: Props) {
  const now = Date.now();

  const columns = useMemo<GridColDef<Position>[]>(() => [
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
      field: 'direction',
      headerName: 'Direction',
      width: 100,
      renderCell: ({ value }) => <DirectionChip direction={value} />,
    },
    {
      field: 'quantity',
      headerName: 'Qté',
      width: 90,
      align: 'right',
      headerAlign: 'right',
      renderCell: ({ value }) => (
        <span className="mono">{fmtNum(value, 4)}</span>
      ),
    },
    {
      field: 'entry_price',
      headerName: 'Entrée',
      width: 110,
      align: 'right',
      headerAlign: 'right',
      renderCell: ({ value }) => <span className="mono">{fmtUsd(value)}</span>,
    },
    {
      field: 'current_price',
      headerName: 'Actuel',
      width: 110,
      align: 'right',
      headerAlign: 'right',
      renderCell: ({ value }) => <span className="mono">{fmtUsd(value)}</span>,
    },
    {
      field: 'value_usd',
      headerName: 'Valeur',
      width: 120,
      align: 'right',
      headerAlign: 'right',
      renderCell: ({ value }) => <span className="mono">{fmtUsd(value)}</span>,
    },
    {
      field: 'unrealized_pnl_usd',
      headerName: 'PnL $',
      width: 120,
      align: 'right',
      headerAlign: 'right',
      renderCell: ({ value, row }) => (
        <Box sx={{ textAlign: 'right' }}>
          <Typography variant="body2" className="mono" sx={{ color: pnlColor(value), fontWeight: 700 }}>
            {fmtUsd(value)}
          </Typography>
          <Typography variant="caption" className="mono" sx={{ color: pnlColor(row.unrealized_pnl_pct) }}>
            {fmtPct(row.unrealized_pnl_pct)}
          </Typography>
        </Box>
      ),
    },
    {
      field: 'stop_loss',
      headerName: 'SL',
      width: 100,
      align: 'right',
      headerAlign: 'right',
      renderCell: ({ value }) => (
        <span className="mono" style={{ color: '#ff5370' }}>
          {value != null ? fmtUsd(value) : '—'}
        </span>
      ),
    },
    {
      field: 'take_profit',
      headerName: 'TP',
      width: 100,
      align: 'right',
      headerAlign: 'right',
      renderCell: ({ value }) => (
        <span className="mono" style={{ color: '#26d07c' }}>
          {value != null ? fmtUsd(value) : '—'}
        </span>
      ),
    },
    {
      field: 'protected',
      headerName: 'Protégé',
      width: 80,
      align: 'center',
      headerAlign: 'center',
      renderCell: ({ value }) =>
        value ? (
          <ShieldIcon fontSize="small" sx={{ color: '#26d07c' }} />
        ) : (
          <ShieldOutlinedIcon fontSize="small" sx={{ color: '#94a0b8' }} />
        ),
    },
    {
      field: 'opened_at',
      headerName: 'Ouvert depuis',
      width: 130,
      renderCell: ({ value }) => (
        <Typography variant="caption" color="text.secondary">
          {fmtRelative(value, now)}
        </Typography>
      ),
    },
  ], [now]);

  return (
    <Card>
      <CardContent>
        <Typography
          variant="subtitle2"
          color="text.secondary"
          sx={{ mb: 2, textTransform: 'uppercase', letterSpacing: 0.6 }}
        >
          Positions ouvertes
        </Typography>

        {isLoading ? (
          <Skeleton variant="rectangular" height={200} sx={{ borderRadius: 1 }} />
        ) : !positions || positions.length === 0 ? (
          <EmptyState message="Aucune position ouverte" />
        ) : (
          <DataGrid
            rows={positions}
            columns={columns}
            getRowId={(row) => row.position_id}
            autoHeight
            density="compact"
            disableRowSelectionOnClick
            hideFooter={positions.length <= 10}
            pageSizeOptions={[10, 25]}
            sx={{
              border: 'none',
              '& .MuiDataGrid-columnHeaders': { background: 'transparent' },
              '& .MuiDataGrid-cell': { borderColor: 'rgba(255,255,255,0.06)' },
              '& .MuiDataGrid-row:hover': { background: 'rgba(255,255,255,0.03)' },
            }}
          />
        )}
      </CardContent>
    </Card>
  );
}
