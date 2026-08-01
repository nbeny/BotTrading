'use client';

import { useState } from 'react';
import {
  Box,
  Card,
  CardContent,
  Chip,
  Divider,
  Skeleton,
  Stack,
  Tooltip,
  Typography,
} from '@mui/material';
import EscalatorWarningIcon from '@mui/icons-material/EscalatorWarning';
import SmartToyIcon from '@mui/icons-material/SmartToy';
import type { WorkerDecision } from '@/lib/types/domain';
import { ScoreChip, EmptyState } from '@/components/common';
import { fmtRelative } from '@/lib/format';

interface Props {
  decisions: WorkerDecision[];
  loading: boolean;
  now: number;
  /** `true` dans le drawer : pas de Card ni de titre, le conteneur les porte. */
  bare?: boolean;
}

const WORKER_META: Record<
  WorkerDecision['worker'],
  { label: string; color: 'warning' | 'primary'; description: string }
> = {
  haiku: { label: 'Claude Haiku', color: 'warning', description: 'Triage rapide — analyse de premier niveau' },
  sonnet: { label: 'Claude Sonnet', color: 'primary', description: 'Décision senior — validation approfondie' },
};

function DecisionCard({ d, now }: { d: WorkerDecision; now: number }) {
  const [expanded, setExpanded] = useState(false);
  const meta = WORKER_META[d.worker];

  return (
    <Box
      sx={{
        p: 2,
        borderRadius: 2,
        border: '1px solid',
        borderColor: d.escalated ? 'warning.dark' : 'rgba(255,255,255,0.07)',
        bgcolor: d.escalated ? 'rgba(255,167,38,0.06)' : 'rgba(255,255,255,0.02)',
        transition: 'border-color 0.2s',
        '&:hover': { borderColor: 'rgba(255,255,255,0.16)' },
      }}
    >
      {/* Card header */}
      <Stack direction="row" justifyContent="space-between" alignItems="center" flexWrap="wrap" gap={1} sx={{ mb: 1.5 }}>
        <Stack direction="row" spacing={1} alignItems="center">
          <Tooltip title={meta.description}>
            <Chip
              icon={<SmartToyIcon sx={{ fontSize: 14 }} />}
              label={meta.label}
              color={meta.color}
              size="small"
              variant="outlined"
            />
          </Tooltip>
          <Typography variant="subtitle2" className="mono" sx={{ fontWeight: 700 }}>
            {d.symbol}
          </Typography>
          {d.escalated && (
            <Tooltip title="Escaladé au niveau supérieur">
              <Chip
                icon={<EscalatorWarningIcon sx={{ fontSize: 14 }} />}
                label="Escaladé"
                color="warning"
                size="small"
              />
            </Tooltip>
          )}
        </Stack>
        <Typography variant="caption" color="text.secondary" className="mono">
          {fmtRelative(d.created_at, now)}
        </Typography>
      </Stack>

      {/* Decision + scores */}
      <Stack direction="row" spacing={2} alignItems="center" sx={{ mb: 1.5 }}>
        <Box
          sx={{
            px: 1.5,
            py: 0.5,
            borderRadius: 1,
            bgcolor: 'rgba(91,141,239,0.12)',
            border: '1px solid rgba(91,141,239,0.2)',
          }}
        >
          <Typography variant="body2" className="mono" sx={{ fontWeight: 700, color: 'primary.light' }}>
            {d.decision}
          </Typography>
        </Box>
        <Stack direction="row" spacing={1} alignItems="center">
          <ScoreChip score={d.opportunity_score} />
          <Typography variant="caption" color="text.secondary">
            conf. {(d.confidence * 100).toFixed(0)}%
          </Typography>
        </Stack>
      </Stack>

      {/* Justification — key feature, displayed prominently; folds to 3 lines so one
          card can't push the rest of the drawer out of view. */}
      <Box
        role="button"
        tabIndex={0}
        aria-expanded={expanded}
        onClick={() => setExpanded((v) => !v)}
        onKeyDown={(e) => {
          if (e.key === 'Enter' || e.key === ' ') {
            e.preventDefault();
            setExpanded((v) => !v);
          }
        }}
        sx={{
          p: 1.5,
          borderRadius: 1.5,
          bgcolor: 'rgba(0,0,0,0.2)',
          border: '1px solid rgba(255,255,255,0.05)',
          cursor: 'pointer',
          '&:focus-visible': {
            outline: '2px solid',
            outlineColor: 'primary.main',
            outlineOffset: 2,
          },
        }}
      >
        <Stack direction="row" alignItems="center" spacing={0.5} sx={{ mb: 0.75 }}>
          <Typography variant="caption" color="text.secondary" sx={{ textTransform: 'uppercase', letterSpacing: 0.6 }}>
            Justification
          </Typography>
          <Typography variant="caption" color="text.secondary" sx={{ fontSize: 10, lineHeight: 1 }}>
            {expanded ? '▲' : '▼'}
          </Typography>
        </Stack>
        <Typography
          variant="body2"
          sx={{
            lineHeight: 1.6,
            color: 'text.primary',
            fontSize: 13,
            whiteSpace: 'pre-wrap',
            wordBreak: 'break-word',
            ...(expanded
              ? {}
              : {
                  display: '-webkit-box',
                  WebkitLineClamp: 3,
                  WebkitBoxOrient: 'vertical',
                  overflow: 'hidden',
                }),
          }}
        >
          {d.justification}
        </Typography>
      </Box>
    </Box>
  );
}

function WorkerGroup({
  worker,
  decisions,
  now,
}: {
  worker: WorkerDecision['worker'];
  decisions: WorkerDecision[];
  now: number;
}) {
  const meta = WORKER_META[worker];
  return (
    <Box>
      <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 1.5 }}>
        <Chip label={meta.label} color={meta.color} size="small" />
        <Typography variant="caption" color="text.secondary">
          {decisions.length} décision{decisions.length > 1 ? 's' : ''}
        </Typography>
      </Stack>
      <Stack spacing={1.5}>
        {decisions.map((d) => (
          <DecisionCard key={d.id} d={d} now={now} />
        ))}
      </Stack>
    </Box>
  );
}

export function WorkerDecisionsPanel({ decisions, loading, now, bare }: Props) {
  // The loading skeleton and the empty state already render without a Card
  // wrapper, so both are `bare`-compatible as-is — nothing to branch on here.
  if (loading) {
    return (
      <Stack spacing={1.5}>
        {Array.from({ length: 4 }).map((_, i) => (
          <Skeleton key={i} variant="rectangular" height={140} sx={{ borderRadius: 2 }} />
        ))}
      </Stack>
    );
  }

  if (decisions.length === 0) {
    return <EmptyState message="Aucune décision worker disponible." />;
  }

  const haikuDecisions = decisions.filter((d) => d.worker === 'haiku');
  const sonnetDecisions = decisions.filter((d) => d.worker === 'sonnet');

  const body = (
    <Stack spacing={3} divider={<Divider sx={{ borderColor: 'rgba(255,255,255,0.06)' }} />}>
      {sonnetDecisions.length > 0 && (
        <WorkerGroup worker="sonnet" decisions={sonnetDecisions} now={now} />
      )}
      {haikuDecisions.length > 0 && (
        <WorkerGroup worker="haiku" decisions={haikuDecisions} now={now} />
      )}
    </Stack>
  );

  if (bare) return body;

  return (
    <Card sx={{ height: '100%' }}>
      <CardContent>
        <Typography variant="h6" sx={{ mb: 2 }}>
          Décisions des workers Claude
        </Typography>
        {body}
      </CardContent>
    </Card>
  );
}
