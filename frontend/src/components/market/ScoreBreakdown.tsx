'use client';

import { Box, LinearProgress, Stack, Tooltip, Typography } from '@mui/material';
import {
  AXIS_LABELS,
  SCORE_AXES,
  axisValue,
  type TokenScore,
} from '@/lib/types/dossier';

interface Props {
  score: TokenScore;
}

/**
 * Les sept axes du scoring, mesurés et non mesurés.
 *
 * Un axe non mesuré rend `—`, jamais `0`. Ce n'est pas une préférence de style :
 * le score renormalise sur le poids des axes présents, donc un axe absent est
 * exclu du calcul. L'afficher à 0 dirait « mesuré, et mauvais » — la lecture
 * exactement inverse de la vérité.
 */
export function ScoreBreakdown({ score }: Props) {
  const measured = SCORE_AXES.filter(
    (a) => axisValue(score.axes, a) !== null,
  ).length;

  return (
    <Box>
      <Stack direction="row" alignItems="baseline" spacing={1} sx={{ mb: 1 }}>
        <Typography variant="h6" className="mono" sx={{ fontWeight: 800 }}>
          {score.value ?? '—'}
        </Typography>
        <Typography variant="caption" color="text.secondary">
          / 100
          {score.confidence !== null &&
            ` · confiance ${(score.confidence * 100).toFixed(0)} %`}
        </Typography>
      </Stack>

      <Stack spacing={0.75}>
        {SCORE_AXES.map((axis) => {
          const v = axisValue(score.axes, axis);
          const absent = v === null;
          return (
            <Stack
              key={axis}
              direction="row"
              alignItems="center"
              spacing={1.5}
              data-testid={`axis-${axis}`}
              sx={{ opacity: absent ? 0.45 : 1 }}
            >
              <Typography variant="caption" sx={{ width: 110, flexShrink: 0 }}>
                {AXIS_LABELS[axis]}
              </Typography>
              <Box sx={{ flex: 1 }}>
                <LinearProgress
                  variant="determinate"
                  value={absent ? 0 : v * 100}
                  sx={{ height: 5, borderRadius: 3 }}
                />
              </Box>
              <Typography
                variant="caption"
                className="mono"
                sx={{ width: 34, textAlign: 'right' }}
              >
                {absent ? '—' : (v * 100).toFixed(0)}
              </Typography>
            </Stack>
          );
        })}
      </Stack>

      {/* Le moteur renvoie un score de 0 quand le poids présent est sous son
          seuil de renormalisation. Le back le convertit en `value: null` ; ici
          on dit pourquoi, sinon « — » se lit comme « jamais analysé ». */}
      {score.insufficient_evidence && (
        <Typography variant="caption" color="warning.main" sx={{ display: 'block', mt: 1 }}>
          Preuves insuffisantes — trop peu d&apos;axes mesurés pour calculer un score
          honnête.
        </Typography>
      )}

      <Tooltip title="Un axe non mesuré est exclu du calcul du score — il n'est pas compté zéro.">
        <Typography
          variant="caption"
          color="text.secondary"
          sx={{ display: 'block', mt: 1 }}
        >
          {measured} axe{measured > 1 ? 's' : ''} sur {score.axes_total} mesuré
          {measured > 1 ? 's' : ''} · « — » = non mesuré, exclu du calcul
        </Typography>
      </Tooltip>
    </Box>
  );
}
