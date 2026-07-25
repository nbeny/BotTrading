'use client';
import { useQuery } from '@tanstack/react-query';
import { Box, Chip, Stack, Typography } from '@mui/material';
import { SectionCard, MiniBar } from '@/components/systems/common';
import { systemsApi } from '@/lib/api/endpoints';
import type { FunnelStage } from '@/lib/types/systems';

const STAGE_LABEL: Record<string, string> = {
  analyses: 'Analyses (triage)',
  escalated: 'Escaladés (Sonnet)',
  decisions: 'Décisions',
  approved: 'Approuvés (risque)',
  executed: 'Exécutés',
};

const REASON_LABEL: Record<string, string> = {
  score_below_threshold: 'score sous le seuil',
  gate_not_met: 'signal trop calme',
};

const ACCENT = '#38bdf8';
const BOTTLENECK_COLOR = '#ff5370';

/** Index of the stage with the largest proportional drop from its
 * predecessor, or null when there's nothing meaningful to flag (no data
 * yet, or a fully healthy funnel with no real drop anywhere). */
function findBottleneck(stages: FunnelStage[]): number | null {
  if (!stages.length || stages[0].count === 0) return null;
  let idx: number | null = null;
  let maxDrop = 0;
  for (let i = 1; i < stages.length; i++) {
    const drop = 100 - stages[i].conversion_pct;
    if (drop > maxDrop) {
      maxDrop = drop;
      idx = i;
    }
  }
  return maxDrop > 0 ? idx : null;
}

export function FunnelPanel() {
  const { data } = useQuery({
    queryKey: ['systems', 'funnel'],
    queryFn: () => systemsApi.funnel(),
    refetchInterval: 30000,
  });

  if (!data) return null;

  const { stages, factors_presence, top_block_reasons } = data;
  const firstCount = stages[0]?.count ?? 0;
  const bottleneckIdx = findBottleneck(stages);

  const factorEntries = ['0', '1', '2', '3', '4'].map((k) => ({ k, n: factors_presence[k] ?? 0 }));
  const factorTotal = factorEntries.reduce((s, f) => s + f.n, 0) || 1;
  const dominantFactor = factorEntries.reduce((a, b) => (b.n > a.n ? b : a), factorEntries[0]);

  return (
    <SectionCard
      title="Entonnoir pipeline"
      subtitle={`Où les signaux meurent · fenêtre ${data.window}`}
      accent={ACCENT}
    >
      <Stack spacing={2}>
        {/* Five stages, bar width proportional to the first stage's count */}
        <Stack spacing={1.25}>
          {stages.map((s, i) => {
            const isBottleneck = i === bottleneckIdx;
            const widthPct = firstCount > 0 ? (s.count / firstCount) * 100 : 0;
            return (
              <Box key={s.stage}>
                <Stack direction="row" justifyContent="space-between" alignItems="center">
                  <Stack direction="row" spacing={0.75} alignItems="center">
                    <Typography variant="caption" color="text.secondary">
                      {STAGE_LABEL[s.stage] ?? s.stage}
                    </Typography>
                    {isBottleneck && (
                      <Chip
                        label="goulot"
                        size="small"
                        sx={{ height: 16, fontSize: 9, bgcolor: 'rgba(255,83,112,0.16)', color: BOTTLENECK_COLOR }}
                      />
                    )}
                  </Stack>
                  <Typography variant="caption" className="mono">
                    {s.count} · {s.conversion_pct.toFixed(1)}%
                  </Typography>
                </Stack>
                <MiniBar pct={widthPct} color={isBottleneck ? BOTTLENECK_COLOR : ACCENT} />
              </Box>
            );
          })}
          {firstCount === 0 && (
            <Typography variant="caption" color="text.secondary">
              Aucune analyse sur cette fenêtre.
            </Typography>
          )}
        </Stack>

        {/* Top block reasons */}
        <Box>
          <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 0.75 }}>
            Principales causes de blocage
          </Typography>
          <Stack direction="row" spacing={0.75} flexWrap="wrap" useFlexGap>
            {top_block_reasons.length ? (
              top_block_reasons.map((r, i) => (
                <Chip
                  key={`${r.stage}-${r.reason}-${i}`}
                  size="small"
                  variant="outlined"
                  label={`${r.stage} · ${REASON_LABEL[r.reason] ?? r.reason} (${r.count})`}
                  sx={{ fontSize: 10, height: 22 }}
                />
              ))
            ) : (
              <Typography variant="caption" color="text.secondary">
                Aucun blocage recensé.
              </Typography>
            )}
          </Stack>
        </Box>

        {/* Factor coverage */}
        <Box>
          <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 0.75 }}>
            Facteurs disponibles par score (sur 4)
          </Typography>
          <Stack direction="row" sx={{ width: '100%', height: 6, borderRadius: 999, overflow: 'hidden', bgcolor: 'rgba(255,255,255,0.06)' }}>
            {factorEntries.map((f) =>
              f.n > 0 ? (
                <Box
                  key={f.k}
                  sx={{
                    width: `${(f.n / factorTotal) * 100}%`,
                    bgcolor: f.k === dominantFactor.k ? BOTTLENECK_COLOR : ACCENT,
                    opacity: 0.35 + Number(f.k) * 0.15,
                  }}
                />
              ) : null,
            )}
          </Stack>
          <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap sx={{ mt: 0.75 }}>
            {factorEntries.map((f) => (
              <Typography
                key={f.k}
                variant="caption"
                className="mono"
                sx={{
                  fontSize: 10,
                  color: f.k === dominantFactor.k ? BOTTLENECK_COLOR : 'text.secondary',
                  fontWeight: f.k === dominantFactor.k ? 700 : 400,
                }}
              >
                {f.k}/4: {f.n}
              </Typography>
            ))}
          </Stack>
        </Box>
      </Stack>
    </SectionCard>
  );
}
