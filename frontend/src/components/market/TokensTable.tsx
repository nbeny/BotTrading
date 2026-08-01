'use client';

import { useMemo, useState } from 'react';
import {
  Box,
  Button,
  Chip,
  MenuItem,
  Skeleton,
  Stack,
  TextField,
  Tooltip,
  Typography,
  useMediaQuery,
  useTheme,
} from '@mui/material';
import { DataGrid, type GridColDef, type GridRowParams } from '@mui/x-data-grid';
import WhatshotIcon from '@mui/icons-material/Whatshot';
import type { MarketToken } from '@/lib/types/domain';
import { fmtUsd, fmtUsdCompact } from '@/lib/format';
import { DeltaText, ScoreChip, SentimentChip, EmptyState } from '@/components/common';
import { SORT_LABELS, filterAndSortTokens, type TokenSortKey } from '@/lib/market/tokensView';

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

/** 15 lignes visibles : au-delà, le tableau repousse le reste de la page hors
 *  écran — le défaut que cette refonte corrige. `autoHeight` est retiré pour
 *  que la hauteur ne dépende jamais du nombre de tokens suivis. */
const VISIBLE_ROWS = 15;
const ROW_HEIGHT = 56;
const HEADER_HEIGHT = 56;
const GRID_HEIGHT = HEADER_HEIGHT + VISIBLE_ROWS * ROW_HEIGHT;

export function TokensTable({ tokens, loading, selectedSymbol, onSelect }: Props) {
  const [query, setQuery] = useState('');
  const [sortKey, setSortKey] = useState<TokenSortKey>('opportunity_score');
  const [showAll, setShowAll] = useState(false);

  const theme = useTheme();
  // Sur petit écran, seules les quatre colonnes qui servent au balayage
  // survivent — le détail est de toute façon dans le drawer.
  const compact = useMediaQuery(theme.breakpoints.down('md'));
  const columnVisibilityModel: Record<string, boolean> = compact
    ? {
        volume_24h_usd: false,
        liquidity_usd: false,
        sentiment_score: false,
        is_trending: false,
      }
    : {};

  const view = useMemo(
    () => filterAndSortTokens(tokens, query, sortKey),
    [tokens, query, sortKey],
  );
  const rows = showAll || query ? view : view.slice(0, VISIBLE_ROWS);

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
    <Box>
      <Stack
        direction="row"
        spacing={1.5}
        alignItems="center"
        flexWrap="wrap"
        useFlexGap
        sx={{ mb: 1.5 }}
      >
        <TextField
          size="small"
          placeholder="Rechercher un token…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          sx={{ minWidth: 200 }}
        />
        <TextField
          size="small"
          select
          label="Trier par"
          value={sortKey}
          onChange={(e) => setSortKey(e.target.value as TokenSortKey)}
          sx={{ minWidth: 160 }}
        >
          {(Object.keys(SORT_LABELS) as TokenSortKey[]).map((k) => (
            <MenuItem key={k} value={k}>
              {SORT_LABELS[k]}
            </MenuItem>
          ))}
        </TextField>
        <Box sx={{ flex: 1 }} />
        <Typography variant="caption" color="text.secondary">
          {rows.length} sur {tokens.length}
        </Typography>
        {!query && tokens.length > VISIBLE_ROWS && (
          <Button size="small" onClick={() => setShowAll((v) => !v)}>
            {showAll ? 'Réduire' : `Voir les ${tokens.length}`}
          </Button>
        )}
      </Stack>

      {/* Hauteur constante : en mode « voir tout », c'est la grille qui scrolle
          en interne, jamais la page. */}
      <Box sx={{ height: GRID_HEIGHT }}>
        <DataGrid<MarketToken>
          rows={rows}
          columns={columns}
          columnVisibilityModel={columnVisibilityModel}
          getRowId={(row) => row.symbol}
          rowHeight={ROW_HEIGHT}
          columnHeaderHeight={HEADER_HEIGHT}
          density="compact"
          disableColumnMenu
          hideFooter
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
      </Box>
    </Box>
  );
}
