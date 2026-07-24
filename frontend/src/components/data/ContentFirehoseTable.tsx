'use client';
import { Box, Chip, Stack, Typography } from '@mui/material';
import type { RawContentItem } from '@/lib/types/content';

function SentPill({ v }: { v: number }) {
  const c = v > 0.15 ? 'success' : v < -0.15 ? 'error' : 'default';
  return <Chip size="small" color={c} variant="outlined" className="mono" label={v.toFixed(2)} sx={{ height: 18, fontSize: 9.5 }} />;
}
export function ContentFirehoseTable({ items, onSelect }: { items: RawContentItem[]; onSelect: (i: RawContentItem) => void }) {
  return (
    <Box>
      {items.map((i) => (
        <Stack key={i.id} direction="row" alignItems="center" spacing={1.5} onClick={() => onSelect(i)}
          sx={{ px: 1, py: 0.9, borderBottom: '1px solid', borderColor: 'divider', cursor: 'pointer', '&:hover': { bgcolor: 'rgba(77,159,255,0.08)' } }}>
          <Typography variant="caption" className="mono" color="text.secondary" sx={{ width: 44 }}>{new Date(i.collected_at).toLocaleTimeString('fr-FR', { hour: '2-digit', minute: '2-digit' })}</Typography>
          <Typography variant="caption" sx={{ color: '#9fe9f5', width: 84 }} noWrap>{i.platform}</Typography>
          <Chip size="small" label={i.source_category} variant="outlined" sx={{ height: 17, fontSize: 9 }} />
          <Typography variant="caption" sx={{ width: 44, fontWeight: 700 }} noWrap>{i.symbols[0] ?? '—'}</Typography>
          <Typography variant="body2" sx={{ flex: 1, minWidth: 0 }} noWrap>{i.title}</Typography>
          <SentPill v={i.sentiment_score} />
          {i.derived_decision?.direction
            ? <Chip size="small" color={i.derived_decision.direction === 'long' ? 'success' : 'error'} label={`→ ${i.derived_decision.direction.toUpperCase()}`} sx={{ height: 18, fontSize: 9 }} />
            : <Typography variant="caption" color="text.secondary" sx={{ width: 54, textAlign: 'right' }}>—</Typography>}
        </Stack>
      ))}
      {!items.length && <Typography variant="body2" color="text.secondary" sx={{ p: 3, textAlign: 'center' }}>Aucun résultat.</Typography>}
    </Box>
  );
}
