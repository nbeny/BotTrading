'use client';

import { Box, Chip, Skeleton, Tooltip, Typography } from '@mui/material';
import { DataGrid, type GridColDef, type GridRowParams } from '@mui/x-data-grid';
import WhatshotIcon from '@mui/icons-material/Whatshot';
import type { MarketToken } from '@/lib/types/domain';
import { fmtUsd, fmtUsdCompact } from '@/lib/format';
import { DeltaText, ScoreChip, SentimentChip, EmptyState } from '@/components/common';

interface Props {
  tokens: MarketToken[];
  loading: boolean;
  selectedSymbol: string | null;
  onSelect: (symbol: string) => void;
}

const columns: GridColDef<MarketToken>[] = [
  {
    field: 'symbol',
    headerName: 'Symbole / Nom',
    flex: 1.2,
    minWidth: 140,
    renderCell: ({ row }) => (
      <Box sx={{ display: 'flex', flexDirection: 'column', justifyContent: 'center', height: '100%' }}>
        <Typography variant="body2" className="mono" sx={{ fontWeight: 700, lineHeight: 1.2 }}>
          {row.symbol}
        </Typography>
        <Typography variant="caption" color="text.secondary" sx={{ lineHeight: 1.2 }}>
          {row.name}
        </Typography>
      </Box>
    ),
  },
  {
    field: 'price_usd',
    headerName: 'Prix',
    flex: 1,
    minWidth: 110,
    align: 'right',
    headerAlign: 'right',
    renderCell: ({ value }) => (
      <Typography variant="body2" className="mono">
        {fmtUsd(value as number)}
      </Typography>
    ),
  },
  {
    field: 'price_change_pct_24h',
    headerName: 'Var. 24h',
    flex: 0.8,
    minWidth: 90,
    align: 'right',
    headerAlign: 'right',
    renderCell: ({ value }) => <DeltaText value={value as number} />,
  },
  {
    field: 'volume_24h_usd',
    headerName: 'Volume 24h',
    flex: 1,
    minWidth: 110,
    align: 'right',
    headerAlign: 'right',
    renderCell: ({ value }) => (
      <Typography variant="body2" className="mono">
        {fmtUsdCompact(value as number)}
      </Typography>
    ),
  },
  {
    field: 'liquidity_usd',
    headerName: 'Liquidité',
    flex: 1,
    minWidth: 110,
    align: 'right',
    headerAlign: 'right',
    renderCell: ({ value }) => (
      <Typography variant="body2" className="mono">
        {fmtUsdCompact(value as number)}
      </Typography>
    ),
  },
  {
    field: 'sentiment_score',
    headerName: 'Sentiment',
    flex: 1,
    minWidth: 130,
    align: 'center',
    headerAlign: 'center',
    renderCell: ({ value }) => <SentimentChip score={value as number} />,
  },
  {
    field: 'opportunity_score',
    headerName: 'Score opp.',
    flex: 0.8,
    minWidth: 100,
    align: 'center',
    headerAlign: 'center',
    renderCell: ({ value }) => <ScoreChip score={value as number} />,
  },
  {
    field: 'is_trending',
    headerName: 'Trending',
    flex: 0.6,
    minWidth: 80,
    align: 'center',
    headerAlign: 'center',
    renderCell: ({ value }) =>
      value ? (
        <Tooltip title="En tendance">
          <Chip
            icon={<WhatshotIcon />}
            label="Hot"
            color="warning"
            size="small"
            variant="outlined"
          />
        </Tooltip>
      ) : null,
  },
];

export function TokensTable({ tokens, loading, selectedSymbol, onSelect }: Props) {
  if (loading) {
    return (
      <Box sx={{ display: 'flex', flexDirection: 'column', gap: 1 }}>
        {Array.from({ length: 6 }).map((_, i) => (
          <Skeleton key={i} variant="rectangular" height={52} sx={{ borderRadius: 1 }} />
        ))}
      </Box>
    );
  }

  if (tokens.length === 0) {
    return <EmptyState message="Aucun token disponible." />;
  }

  return (
    <DataGrid<MarketToken>
      rows={tokens}
      columns={columns}
      getRowId={(row) => row.symbol}
      rowHeight={56}
      density="compact"
      autoHeight
      disableColumnMenu
      hideFooterSelectedRowCount
      rowSelectionModel={selectedSymbol ? [selectedSymbol] : []}
      onRowClick={(params: GridRowParams<MarketToken>) => onSelect(params.row.symbol)}
      sx={{
        border: 'none',
        '& .MuiDataGrid-row': {
          cursor: 'pointer',
        },
        '& .MuiDataGrid-row.Mui-selected': {
          bgcolor: 'rgba(91,141,239,0.12)',
        },
        '& .MuiDataGrid-row:hover': {
          bgcolor: 'rgba(255,255,255,0.04)',
        },
        '& .MuiDataGrid-columnHeaders': {
          borderBottom: '1px solid rgba(255,255,255,0.08)',
        },
        '& .MuiDataGrid-cell': {
          borderBottom: '1px solid rgba(255,255,255,0.04)',
        },
      }}
    />
  );
}
