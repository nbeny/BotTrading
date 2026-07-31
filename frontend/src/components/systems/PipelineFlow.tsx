'use client';

import { Box, Stack, Tooltip, Typography } from '@mui/material';
import { HEALTH_COLOR, HealthDot } from './common';
import { HEALTH_LABEL, type PipelineStage } from '@/lib/types/systems';
import { fmtConversion, fmtNum, fmtRate, fmtRelative } from '@/lib/format';

/** Below this, a stage-to-stage survival rate is a collapse worth flagging. */
const CONVERSION_ALERT_THRESHOLD = 10;
const ALERT_COLOR = '#ff5370';

function Connector({ nextStage }: { nextStage: PipelineStage }) {
  const conversionText = fmtConversion(nextStage.conversion_pct);
  const conversionAlert =
    nextStage.conversion_pct != null && nextStage.conversion_pct < CONVERSION_ALERT_THRESHOLD;
  return (
    <Box
      sx={{
        position: 'relative',
        flex: '0 0 auto',
        width: { xs: 54, md: 72 },
        height: 118,
        display: 'grid',
        placeItems: 'center',
      }}
    >
      <svg width="100%" height="40" viewBox="0 0 72 40" preserveAspectRatio="none" aria-hidden>
        <line x1="0" y1="20" x2="66" y2="20" stroke="rgba(255,255,255,0.12)" strokeWidth="2" />
        <line
          x1="0"
          y1="20"
          x2="66"
          y2="20"
          stroke="#4d9fff"
          strokeWidth="2"
          className="flow-dash"
          opacity="0.9"
        />
        <path d="M66 20 L58 16 L58 24 Z" fill="#4d9fff" />
      </svg>
      <Typography
        className="mono"
        sx={{
          position: 'absolute',
          top: 10,
          fontSize: 10,
          color: 'text.secondary',
          whiteSpace: 'nowrap',
        }}
      >
        {fmtRate(nextStage.throughput_per_min)}
      </Typography>
      {conversionText && (
        <Typography
          className="mono"
          sx={{
            position: 'absolute',
            bottom: 10,
            fontSize: 10,
            fontWeight: 700,
            color: conversionAlert ? ALERT_COLOR : 'text.secondary',
            whiteSpace: 'nowrap',
          }}
        >
          {conversionText}
        </Typography>
      )}
    </Box>
  );
}

function Node({
  stage,
  index,
  now,
  onSelect,
}: {
  stage: PipelineStage;
  index: number;
  now: number;
  onSelect: (id: string) => void;
}) {
  const color = HEALTH_COLOR[stage.status];
  // 0 is a measured, meaningful "nothing happened" — it must read as a
  // sentence, not as a bare digit indistinguishable from "not measured".
  const volumeText = stage.volume === 0 ? 'aucun élément' : fmtNum(stage.volume, 0);
  const showDropped = stage.dropped != null && stage.dropped > 0;

  const handleKeyDown = (e: React.KeyboardEvent<HTMLDivElement>) => {
    if (e.key === 'Enter' || e.key === ' ') {
      e.preventDefault();
      onSelect(stage.id);
    }
  };

  return (
    <Tooltip
      title={`${stage.label} — ${HEALTH_LABEL[stage.status]} · ${fmtRate(stage.throughput_per_min)}`}
    >
      <Box
        role="button"
        tabIndex={0}
        onClick={() => onSelect(stage.id)}
        onKeyDown={handleKeyDown}
        className="reveal"
        sx={{
          ['--d' as string]: `${index * 70}ms`,
          flex: '0 0 auto',
          width: { xs: 130, md: 148 },
          height: 118,
          borderRadius: 3,
          p: 1.5,
          display: 'flex',
          flexDirection: 'column',
          justifyContent: 'space-between',
          position: 'relative',
          cursor: 'pointer',
          background: `linear-gradient(160deg, ${color}1a, rgba(255,255,255,0.02))`,
          border: `1px solid ${color}40`,
          boxShadow: `0 0 0 1px ${color}10, 0 8px 24px rgba(0,0,0,0.35)`,
          transition: 'border-color .16s ease, transform .16s ease, box-shadow .16s ease',
          '&:hover': {
            borderColor: `${color}80`,
            transform: 'translateY(-2px)',
          },
          '&:focus-visible': {
            outline: `2px solid ${color}`,
            outlineOffset: 2,
          },
        }}
      >
        <Stack direction="row" alignItems="center" justifyContent="space-between">
          <Stack direction="row" alignItems="center" spacing={0.75}>
            <HealthDot status={stage.status} size={8} />
            <Typography variant="caption" sx={{ fontWeight: 700, fontSize: 10.5, color: 'text.secondary' }}>
              {String(index + 1).padStart(2, '0')}
            </Typography>
          </Stack>
          <Typography variant="caption" color="text.secondary" sx={{ fontSize: 10, whiteSpace: 'nowrap' }}>
            {fmtRelative(stage.last_at, now)}
          </Typography>
        </Stack>
        <Box>
          <Typography sx={{ fontWeight: 700, fontSize: 14, lineHeight: 1.1, fontFamily: '"Sora", sans-serif' }}>
            {stage.label}
          </Typography>
          <Typography variant="caption" color="text.secondary" noWrap sx={{ display: 'block', fontSize: 10.5 }}>
            {stage.sublabel}
          </Typography>
        </Box>
        <Stack direction="row" alignItems="baseline" spacing={0.75}>
          <Typography
            className="mono"
            sx={{
              fontSize: 12.5,
              fontWeight: 700,
              minWidth: 0,
              flex: 1,
              overflow: 'hidden',
              textOverflow: 'ellipsis',
              whiteSpace: 'nowrap',
            }}
          >
            {volumeText}
          </Typography>
          {showDropped && (
            <Typography
              className="mono"
              sx={{ fontSize: 11, fontWeight: 700, color: ALERT_COLOR, flexShrink: 0, whiteSpace: 'nowrap' }}
            >
              ▼ {fmtNum(stage.dropped, 0)}
            </Typography>
          )}
        </Stack>
      </Box>
    </Tooltip>
  );
}

export function PipelineFlow({ stages, onSelect }: { stages: PipelineStage[]; onSelect: (id: string) => void }) {
  // One clock for the whole row — per-node `Date.now()` would make sibling
  // "il y a Xm" ages disagree from render to render.
  const now = Date.now();
  return (
    <Box
      sx={{
        display: 'flex',
        alignItems: 'center',
        overflowX: 'auto',
        // A flex item defaults to `min-width: auto`, which means it refuses to
        // shrink below its content. Without this the overflowX above never
        // engages: instead of scrolling, the row pushes the whole Command
        // Center column wider than the viewport below ~1650px.
        minWidth: 0,
        pb: 1,
        // hide native scrollbar chrome but keep scroll
        '&::-webkit-scrollbar': { height: 6 },
      }}
    >
      {stages.map((stage, i) => (
        <Box key={stage.id} sx={{ display: 'flex', alignItems: 'center' }}>
          <Node stage={stage} index={i} now={now} onSelect={onSelect} />
          {i < stages.length - 1 && <Connector nextStage={stages[i + 1]} />}
        </Box>
      ))}
    </Box>
  );
}
