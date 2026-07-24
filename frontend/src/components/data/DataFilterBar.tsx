'use client';
import { Box, ToggleButton, ToggleButtonGroup, TextField, MenuItem } from '@mui/material';
import type { ContentCategory, ContentQuery } from '@/lib/types/content';

export function DataFilterBar({ value, onChange }: { value: ContentQuery; onChange: (q: ContentQuery) => void }) {
  return (
    <Box sx={{ display: 'flex', gap: 1.5, flexWrap: 'wrap', alignItems: 'center' }}>
      <ToggleButtonGroup size="small" exclusive value={value.category ?? 'all'} onChange={(_, v) => v && onChange({ ...value, category: v as ContentCategory })}>
        <ToggleButton value="all">Tout</ToggleButton><ToggleButton value="social">Social</ToggleButton>
        <ToggleButton value="news">News</ToggleButton><ToggleButton value="market">Marché</ToggleButton>
      </ToggleButtonGroup>
      <TextField size="small" placeholder="🔍 recherche" value={value.q ?? ''} onChange={(e) => onChange({ ...value, q: e.target.value })} sx={{ minWidth: 180 }} />
      <TextField size="small" placeholder="token (BTC)" value={value.symbol ?? ''} onChange={(e) => onChange({ ...value, symbol: e.target.value || undefined })} sx={{ width: 130 }} />
      <TextField size="small" select label="Sentiment" value={value.sentiment ?? 'all'} onChange={(e) => onChange({ ...value, sentiment: e.target.value as ContentQuery['sentiment'] })} sx={{ width: 140 }}>
        <MenuItem value="all">Tous</MenuItem><MenuItem value="pos">Positif</MenuItem><MenuItem value="neu">Neutre</MenuItem><MenuItem value="neg">Négatif</MenuItem>
      </TextField>
    </Box>
  );
}
