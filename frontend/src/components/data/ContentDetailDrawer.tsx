'use client';
import { Drawer, Stack, Typography, Chip, IconButton, Divider } from '@mui/material';
import CloseIcon from '@mui/icons-material/Close';
import type { RawContentItem } from '@/lib/types/content';

export function ContentDetailDrawer({ item, onOpenTrace, onClose }: { item: RawContentItem | null; onOpenTrace: (cid: string) => void; onClose: () => void }) {
  return (
    <Drawer anchor="right" open={!!item} onClose={onClose}
      PaperProps={{ sx: { width: { xs: '100%', sm: 440 }, bgcolor: 'rgba(8,11,20,0.92)', backdropFilter: 'blur(16px)', p: 2.5 } }}>
      {item && (
        <>
          <Stack direction="row" justifyContent="space-between" alignItems="center" sx={{ mb: 2 }}>
            <Typography variant="overline" color="text.secondary">{item.platform} · {item.source_category}</Typography>
            <IconButton onClick={onClose}><CloseIcon /></IconButton>
          </Stack>
          <Typography variant="h6" sx={{ mb: 1 }}>{item.title}</Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>{item.snippet}</Typography>
          <Stack direction="row" spacing={1} sx={{ mb: 2 }} flexWrap="wrap" useFlexGap>
            {item.symbols.map((s) => <Chip key={s} size="small" label={s} />)}
          </Stack>
          <Divider sx={{ my: 2 }} />
          <Typography variant="overline" color="text.secondary">Sentiment</Typography>
          <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap sx={{ mb: 2 }}>
            <Chip size="small" className="mono" label={`score ${item.sentiment_score.toFixed(2)}`} />
            <Chip size="small" className="mono" label={`conf ${Math.round(item.sentiment_confidence * 100)}%`} />
            <Chip size="small" className="mono" label={item.model_name} />
            <Chip size="small" className="mono" label={`n=${item.sample_size}`} />
          </Stack>
          {item.derived_decision?.correlation_id && (
            <Chip color="primary" variant="outlined" label="Voir la trace décisionnelle →"
              onClick={() => onOpenTrace(item.derived_decision!.correlation_id!)} />
          )}
        </>
      )}
    </Drawer>
  );
}
