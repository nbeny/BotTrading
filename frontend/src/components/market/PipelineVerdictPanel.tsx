'use client';

import { Box, Chip, Stack, Typography } from '@mui/material';
import type { PipelineVerdict } from '@/lib/types/dossier';

/** Vocabulaire aligné sur api-gateway/app/systems_pipeline.py::STAGE_SPECS. */
const STAGE_LABELS: Record<string, string> = {
  collect: 'Collecte',
  sentiment: 'Sentiment',
  triage: 'Triage (Haiku)',
  senior: 'Analyse (Sonnet)',
  decision: 'Décision',
  risk: 'Risque',
  execute: 'Exécution',
};

function label(stage: string | null): string {
  if (stage === null) return '—';
  return STAGE_LABELS[stage] ?? stage;
}

interface Props {
  verdict: PipelineVerdict;
}

/**
 * Où le dernier signal du token s'est arrêté, et pourquoi.
 *
 * `blocked_at === null` ne veut pas dire « passé » : le signal peut être encore
 * en vol. Les deux états sont rendus différemment — affirmer un succès non
 * observé serait la même faute que rapporter un zéro non mesuré.
 *
 * De même, `escalated`/`sonnet_called` à `null` ne rendent **rien** : c'est
 * l'état « on ne sait pas », qui ne doit pas se lire comme un « non ».
 */
export function PipelineVerdictPanel({ verdict }: Props) {
  if (verdict.reached_stage === null) {
    return (
      <Typography variant="body2" color="text.secondary">
        Jamais analysé — aucun signal enregistré pour ce token.
      </Typography>
    );
  }

  const blocked = verdict.blocked_at !== null;

  return (
    <Box>
      <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap" useFlexGap>
        <Chip size="small" label={`Atteint : ${label(verdict.reached_stage)}`} />
        {verdict.escalated === true && (
          <Chip size="small" color="warning" variant="outlined" label="Escaladé" />
        )}
        {verdict.sonnet_called === true && (
          <Chip
            size="small"
            color={verdict.sonnet_validated ? 'success' : 'default'}
            variant="outlined"
            label={
              verdict.sonnet_validated === null
                ? 'Sonnet — verdict inconnu'
                : verdict.sonnet_validated
                  ? 'Sonnet — validé'
                  : 'Sonnet — non validé'
            }
          />
        )}
      </Stack>

      <Typography variant="body2" sx={{ mt: 1 }}>
        {blocked ? (
          <>
            Bloqué à <b>{label(verdict.blocked_at)}</b>
            {verdict.block_reason && (
              <>
                {' — motif : '}
                <code>{verdict.block_reason}</code>
              </>
            )}
          </>
        ) : (
          'Aucun blocage observé — le signal a pu poursuivre, ou être encore en cours de traitement.'
        )}
      </Typography>
    </Box>
  );
}
