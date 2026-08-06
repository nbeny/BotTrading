'use client';

import { DataGrid, type GridColDef, type GridRowParams } from '@mui/x-data-grid';
import { Box, Chip, Typography } from '@mui/material';
import { EmptyState } from '@/components/common';
import type { JournalRow } from '@/lib/types/journal';
import { fmtDateTime } from '@/lib/format';

const VISIBLE_ROWS = 12;
const ROW_HEIGHT = 44;
const HEADER_HEIGHT = 48;
const GRID_HEIGHT = HEADER_HEIGHT + VISIBLE_ROWS * ROW_HEIGHT;

/** A flat mark at horizon is not a loss: 0 renders neutral, never the red of
 *  a genuine drawdown — the house rule (null → '—' dimmed, never a confident
 *  zero) has a sibling here: a measured zero must not read as a measured
 *  negative either. */
export function pnlTone(pnl: number | null): 'success.main' | 'error.main' | 'text.primary' | 'text.disabled' {
  if (pnl === null) return 'text.disabled';
  if (pnl > 0) return 'success.main';
  if (pnl < 0) return 'error.main';
  return 'text.primary';
}

const COLUMNS: GridColDef<JournalRow>[] = [
  { field: 'time', headerName: 'Date', width: 150, valueFormatter: (v: string | null) => (v ? fmtDateTime(v) : '—') },
  { field: 'symbol', headerName: 'Symbole', width: 90 },
  { field: 'score', headerName: 'Score', width: 70, valueFormatter: (v: number | null) => v ?? '—' },
  { field: 'direction', headerName: 'Dir.', width: 70, valueFormatter: (v: string | null) => v ?? '—' },
  {
    field: 'passed', headerName: 'Verdict', width: 110,
    renderCell: (p) => (
      <Chip size="small" variant="outlined" color={p.row.passed ? 'success' : 'default'} label={p.row.passed ? (p.row.risk_verdict ?? 'passé') : 'rejeté'} />
    ),
  },
  {
    field: 'pnl_pct', headerName: 'PnL simulé', width: 110,
    renderCell: (p) => (
      <Typography variant="body2" className="mono" color={pnlTone(p.row.pnl_pct)}>
        {p.row.pnl_pct === null ? '—' : `${p.row.pnl_pct > 0 ? '+' : ''}${p.row.pnl_pct}%`}
      </Typography>
    ),
  },
  { field: 'outcome', headerName: 'Résultat', width: 110, valueFormatter: (v: string | null) => v ?? '—' },
];

export function JournalTable({ rows, loading, onSelect }: {
  rows: JournalRow[];
  loading: boolean;
  onSelect: (eventId: string) => void;
}) {
  if (!loading && rows.length === 0) return <EmptyState message="Aucune décision jugée sur la fenêtre." />;
  return (
    <Box sx={{ height: GRID_HEIGHT }}>
      <DataGrid
        rows={rows}
        columns={COLUMNS}
        getRowId={(r) => r.event_id}
        loading={loading}
        rowHeight={ROW_HEIGHT}
        columnHeaderHeight={HEADER_HEIGHT}
        density="compact"
        disableColumnMenu
        hideFooter
        onRowClick={(p: GridRowParams<JournalRow>) => onSelect(p.row.event_id)}
        sx={{ border: 0, '& .MuiDataGrid-row:hover': { cursor: 'pointer' } }}
      />
    </Box>
  );
}
