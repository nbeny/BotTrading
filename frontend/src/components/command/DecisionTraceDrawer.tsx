'use client';
import { useQuery } from '@tanstack/react-query';
import { Box, CircularProgress, Drawer, Stack, Typography, IconButton, Chip } from '@mui/material';
import CloseIcon from '@mui/icons-material/Close';
import { traceApi } from '@/lib/api/endpoints';
import { apiErrorMessage } from '@/lib/api/client';

const KIND_LABEL: Record<string, string> = { price: 'Prix', sentiment: 'Sentiment', analysis: 'Haiku', decision: 'Sonnet', risk: 'Risque', order: 'Ordre' };

export function DecisionTraceDrawer({ correlationId, onClose }: { correlationId: string | null; onClose: () => void }) {
  const { data, isLoading, isError, error } = useQuery({
    queryKey: ['trace', correlationId],
    queryFn: () => traceApi.get(correlationId!),
    enabled: !!correlationId,
  });

  // A trace where nothing was reached is a real answer, not a blank panel: the
  // event exists but the pipeline recorded no lineage for it. Rendering the six
  // empty stages instead is what made an unresolved trace look like a broken
  // drawer rather than an honest "rien en aval".
  const anyReached = data?.stages.some((s) => s.reached) ?? false;

  return (
    <Drawer anchor="right" open={!!correlationId} onClose={onClose}
      PaperProps={{ sx: { width: { xs: '100%', sm: 460 }, bgcolor: 'rgba(8,11,20,0.92)', backdropFilter: 'blur(16px)', p: 2.5 } }}>
      <Stack direction="row" justifyContent="space-between" alignItems="center" sx={{ mb: 2 }}>
        <Box>
          <Typography variant="overline" color="text.secondary">Trace décisionnelle</Typography>
          <Typography variant="h6">{data?.symbol && data.symbol !== '?' ? data.symbol : '—'}</Typography>
        </Box>
        <IconButton onClick={onClose}><CloseIcon /></IconButton>
      </Stack>

      {isLoading && (
        <Stack alignItems="center" spacing={1.5} sx={{ p: 4 }}>
          <CircularProgress size={18} />
          <Typography variant="body2" color="text.secondary">Reconstruction de la lignée…</Typography>
        </Stack>
      )}

      {isError && (
        <Typography variant="body2" color="error.main" sx={{ p: 2 }}>
          Trace indisponible — {apiErrorMessage(error, 'la requête a échoué')}
        </Typography>
      )}

      {data && !anyReached && (
        <Typography variant="body2" color="text.secondary" sx={{ p: 2 }}>
          Aucune lignée enregistrée pour cet événement : il n&apos;a atteint ni triage, ni
          décision, ni exécution.
        </Typography>
      )}

      {data && anyReached && (
        <Stack spacing={0}>
          {data.stages.map((s, i) => {
            // Unmeasured values are pruned server-side; this guards the same
            // thing client-side so a stale gateway cannot print `score: null`.
            const detail = Object.entries(s.detail).filter(([, v]) => v !== null && v !== undefined);
            return (
              <Box key={s.kind} sx={{ pl: 2, pb: 2, borderLeft: '2px solid', borderColor: s.reached ? 'primary.main' : 'divider', position: 'relative' }}>
                <Box sx={{ position: 'absolute', left: -6, top: 2, width: 10, height: 10, borderRadius: '50%', bgcolor: s.reached ? 'primary.main' : 'text.disabled' }} />
                <Stack direction="row" spacing={1} alignItems="center">
                  <Chip size="small" label={`${i + 1} · ${KIND_LABEL[s.kind] ?? s.kind}`} sx={{ height: 20, fontSize: 10 }} />
                  {s.at && <Typography variant="caption" color="text.secondary" className="mono">{new Date(s.at).toLocaleTimeString('fr-FR')}</Typography>}
                </Stack>
                <Typography variant="body2" sx={{ mt: 0.5 }}>{s.summary}</Typography>
                <Stack direction="row" flexWrap="wrap" useFlexGap sx={{ gap: 0.5, mt: 0.5 }}>
                  {detail.map(([k, v]) => (
                    <Chip key={k} size="small" variant="outlined" className="mono" label={`${k}: ${v}`} sx={{ height: 18, fontSize: 9.5 }} />
                  ))}
                </Stack>
              </Box>
            );
          })}
        </Stack>
      )}
    </Drawer>
  );
}
