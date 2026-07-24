'use client';
import { useQuery } from '@tanstack/react-query';
import { Box, Drawer, Stack, Typography, IconButton, Chip } from '@mui/material';
import CloseIcon from '@mui/icons-material/Close';
import { traceApi } from '@/lib/api/endpoints';

const KIND_LABEL: Record<string, string> = { price: 'Prix', sentiment: 'Sentiment', analysis: 'Haiku', decision: 'Sonnet', risk: 'Risque', order: 'Ordre' };

export function DecisionTraceDrawer({ correlationId, onClose }: { correlationId: string | null; onClose: () => void }) {
  const { data } = useQuery({ queryKey: ['trace', correlationId], queryFn: () => traceApi.get(correlationId!), enabled: !!correlationId });
  return (
    <Drawer anchor="right" open={!!correlationId} onClose={onClose}
      PaperProps={{ sx: { width: { xs: '100%', sm: 460 }, bgcolor: 'rgba(8,11,20,0.92)', backdropFilter: 'blur(16px)', p: 2.5 } }}>
      <Stack direction="row" justifyContent="space-between" alignItems="center" sx={{ mb: 2 }}>
        <Box>
          <Typography variant="overline" color="text.secondary">Trace décisionnelle</Typography>
          <Typography variant="h6">{data?.symbol ?? '…'}</Typography>
        </Box>
        <IconButton onClick={onClose}><CloseIcon /></IconButton>
      </Stack>
      <Stack spacing={0}>
        {data?.stages.map((s, i) => (
          <Box key={s.kind} sx={{ pl: 2, pb: 2, borderLeft: '2px solid', borderColor: s.reached ? 'primary.main' : 'divider', position: 'relative' }}>
            <Box sx={{ position: 'absolute', left: -6, top: 2, width: 10, height: 10, borderRadius: '50%', bgcolor: s.reached ? 'primary.main' : 'text.disabled' }} />
            <Stack direction="row" spacing={1} alignItems="center">
              <Chip size="small" label={`${i + 1} · ${KIND_LABEL[s.kind] ?? s.kind}`} sx={{ height: 20, fontSize: 10 }} />
              {s.at && <Typography variant="caption" color="text.secondary" className="mono">{new Date(s.at).toLocaleTimeString('fr-FR')}</Typography>}
            </Stack>
            <Typography variant="body2" sx={{ mt: 0.5 }}>{s.summary}</Typography>
            <Stack direction="row" flexWrap="wrap" useFlexGap sx={{ gap: 0.5, mt: 0.5 }}>
              {Object.entries(s.detail).map(([k, v]) => (
                <Chip key={k} size="small" variant="outlined" className="mono" label={`${k}: ${v}`} sx={{ height: 18, fontSize: 9.5 }} />
              ))}
            </Stack>
          </Box>
        ))}
      </Stack>
    </Drawer>
  );
}
