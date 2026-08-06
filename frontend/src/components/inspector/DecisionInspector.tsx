'use client';

import { Suspense } from 'react';
import { useQuery } from '@tanstack/react-query';
import {
  Box, Chip, CircularProgress, Divider, Drawer, IconButton, LinearProgress,
  Stack, Typography,
} from '@mui/material';
import CloseIcon from '@mui/icons-material/Close';
import { explainApi } from '@/lib/api/endpoints';
import { apiErrorMessage } from '@/lib/api/client';
import { useDecisionParam } from '@/lib/hooks/useDecisionParam';
import { AXIS_LABELS, SCORE_AXES, axisValue } from '@/lib/types/dossier';
import { KIND_LABEL } from '@/lib/types/trace';

function InspectorContent() {
  const { decisionId, close } = useDecisionParam();
  const { data, isLoading, error } = useQuery({
    queryKey: ['decision', 'explain', decisionId],
    queryFn: () => explainApi.get(decisionId!),
    enabled: !!decisionId,
  });

  return (
    <Drawer
      anchor="right"
      open={!!decisionId}
      onClose={close}
      PaperProps={{ sx: { width: { xs: '100%', sm: 560 }, bgcolor: 'rgba(8,11,20,0.92)', backdropFilter: 'blur(16px)', p: 2.5 } }}
    >
      <Stack direction="row" justifyContent="space-between" alignItems="center">
        <Typography variant="overline" sx={{ letterSpacing: 2 }}>Inspecteur de décision</Typography>
        <IconButton aria-label="Fermer" onClick={close}><CloseIcon /></IconButton>
      </Stack>
      {isLoading && (
        <Stack alignItems="center" sx={{ py: 6 }}><CircularProgress size={28} /></Stack>
      )}
      {error != null && (
        <Typography color="error" sx={{ mt: 2 }}>
          Décision introuvable ou requête échouée — {apiErrorMessage(error, 'la requête a échoué')}
        </Typography>
      )}
      {data && (
        <Stack spacing={2.5} sx={{ mt: 1 }}>
          {/* En-tête : échelle brute 0-100 uniquement */}
          <Stack direction="row" spacing={1.5} alignItems="baseline">
            <Typography variant="h5">{data.symbol ?? '—'}</Typography>
            {data.direction && <Chip size="small" label={data.direction.toUpperCase()} />}
            <Typography variant="h4" className="mono">{data.score.value ?? '—'}</Typography>
            <Typography variant="caption" sx={{ opacity: 0.6 }}>/100 · conf {data.score.confidence ?? '—'}</Typography>
          </Stack>

          {/* Waterfall des axes — rendu depuis SCORE_AXES, agnostique au nombre.
              « pas de décision » (insufficient_evidence) et « pas d'axes »
              (journal-only, rejeté avant scoring) sont deux cas distincts qui
              se rabattent tous deux sur le même message : ni l'un ni l'autre
              n'a de breakdown exploitable. */}
          <Box>
            <Typography variant="subtitle2" sx={{ mb: 1 }}>Axes de scoring</Typography>
            {data.score.insufficient_evidence || Object.keys(data.score.axes).length === 0 ? (
              <Typography variant="body2" sx={{ opacity: 0.7 }}>
                breakdown indisponible (décision pré-v2 ou rejetée avant scoring)
              </Typography>
            ) : (
              SCORE_AXES.map((axis) => {
                const v = axisValue(data.score.axes, axis);
                return (
                  <Stack key={axis} direction="row" spacing={1} alignItems="center" sx={{ mb: 0.5 }}>
                    <Typography variant="caption" sx={{ width: 120, opacity: 0.7 }}>{AXIS_LABELS[axis]}</Typography>
                    {v === null ? (
                      <Typography variant="caption" sx={{ opacity: 0.5 }}>— (absent, exclu du score)</Typography>
                    ) : (
                      <>
                        <LinearProgress variant="determinate" value={v * 100} sx={{ flex: 1, height: 6, borderRadius: 3 }} />
                        <Typography variant="caption" className="mono">{v.toFixed(2)}</Typography>
                      </>
                    )}
                  </Stack>
                );
              })
            )}
          </Box>

          {/* Triage — namespace disjoint, étiqueté comme tel */}
          {data.triage && (
            <Box>
              <Typography variant="subtitle2">Triage Haiku <Typography component="span" variant="caption" sx={{ opacity: 0.5 }}>(facteurs de triage — distincts des axes)</Typography></Typography>
              <Stack direction="row" spacing={1} sx={{ mt: 0.5, flexWrap: 'wrap' }}>
                {Object.entries(data.triage.factors).map(([k, v]) => (
                  <Chip key={k} size="small" variant="outlined" label={`${k} ${typeof v === 'number' ? v.toFixed(2) : '—'}`} />
                ))}
              </Stack>
              <Typography variant="caption" sx={{ opacity: 0.7 }}>
                escaladé : {data.triage.escalated ? 'oui' : 'non'} · Sonnet : {data.triage.sonnet_called ? (data.triage.sonnet_validated === null ? 'appelé' : data.triage.sonnet_validated ? 'validé' : 'refusé') : 'non appelé'}
                {data.triage.skip_reason ? ` · skip : ${data.triage.skip_reason}` : ''}
              </Typography>
            </Box>
          )}

          {/* Verdict risque */}
          {data.risk && (
            <Box>
              <Typography variant="subtitle2">Risque</Typography>
              <Typography variant="body2">
                {data.risk.verdict ?? '—'}{data.risk.reason ? ` — ${data.risk.reason}` : ''}
              </Typography>
            </Box>
          )}

          {/* Contrefactuel */}
          <Box>
            <Typography variant="subtitle2">Contrefactuel</Typography>
            {data.counterfactual ? (
              <Typography variant="body2" className="mono">
                {data.counterfactual.outcome ?? '—'} · {data.counterfactual.pnl_pct !== null ? `${data.counterfactual.pnl_pct > 0 ? '+' : ''}${data.counterfactual.pnl_pct}%` : '—'} @ {data.counterfactual.horizon}
              </Typography>
            ) : (
              <Typography variant="body2" sx={{ opacity: 0.6 }}>non jugé (pas de niveaux entry/SL/TP)</Typography>
            )}
          </Box>

          <Divider />

          {/* Timeline */}
          <Box>
            <Typography variant="subtitle2" sx={{ mb: 1 }}>Timeline</Typography>
            {data.trace ? (
              data.trace.stages.map((s, i) => (
                <Stack key={i} direction="row" spacing={1} alignItems="center" sx={{ mb: 0.5, opacity: s.reached ? 1 : 0.4 }}>
                  <Chip size="small" label={`${i + 1} · ${KIND_LABEL[s.kind] ?? s.kind}`} />
                  <Typography variant="caption">{s.summary}</Typography>
                </Stack>
              ))
            ) : (
              <Typography variant="body2" sx={{ opacity: 0.6 }}>
                pas de lineage par correlation id (~95 % du flux) — lien par (symbole, temps) non disponible pour cette décision
              </Typography>
            )}
          </Box>
        </Stack>
      )}
    </Drawer>
  );
}

export function DecisionInspector() {
  return (
    <Suspense fallback={null}>
      <InspectorContent />
    </Suspense>
  );
}
