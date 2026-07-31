'use client';

import { useEffect, useRef, useState } from 'react';
import { Box, Stack, Tooltip, Typography } from '@mui/material';
import { HealthDot } from './common';
import { HEALTH_LABEL, type PipelineStage } from '@/lib/types/systems';
import { fmtConversion, fmtNum, fmtRate, fmtRelative } from '@/lib/format';
import {
  activityColor,
  activityRatio,
  activityRgb,
  busiestVolume,
  rgba,
  STAGE_BY_EVENT,
} from '@/lib/activity';
import { useEventSubscription } from '@/lib/ws/WebSocketProvider';

/** Below this, a stage-to-stage survival rate is a collapse worth flagging. */
const CONVERSION_ALERT_THRESHOLD = 10;
const ALERT_COLOR = '#ff5370';

/** How long a stage stays lit after an event lands on it. */
const PULSE_DECAY_MS = 1400;
/** Fast enough to read as a flicker rather than a blink. */
const PULSE_TICK_MS = 90;

/**
 * Live brightness per stage, fed by the broadcast WebSocket and decaying on its
 * own.
 *
 * The counted volumes underneath refresh every few seconds; this is what makes
 * the graph feel like it is running *now*. It only ever adds brightness — a
 * stage with no live events still shows its counted colour, because several
 * stages are invisible on the wire (see STAGE_BY_EVENT) and none of them may
 * look dead for that reason.
 */
function useStagePulses(): Record<string, number> {
  const lastSeen = useRef<Record<string, number>>({});
  const [pulses, setPulses] = useState<Record<string, number>>({});

  useEventSubscription([], (event) => {
    const stage = STAGE_BY_EVENT[event.event_type];
    if (stage) lastSeen.current[stage] = Date.now();
  });

  useEffect(() => {
    const id = setInterval(() => {
      const now = Date.now();
      const next: Record<string, number> = {};
      for (const [stage, at] of Object.entries(lastSeen.current)) {
        const value = Math.max(0, 1 - (now - at) / PULSE_DECAY_MS);
        if (value > 0) next[stage] = value;
      }
      // Returning the previous object when nothing is fading keeps an idle
      // graph from re-rendering seven nodes ten times a second.
      setPulses((prev) =>
        Object.keys(next).length || Object.keys(prev).length ? next : prev,
      );
    }, PULSE_TICK_MS);
    return () => clearInterval(id);
  }, []);

  return pulses;
}

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
  ratio,
  pulse,
  onSelect,
}: {
  stage: PipelineStage;
  index: number;
  now: number;
  ratio: number;
  pulse: number;
  onSelect: (id: string) => void;
}) {
  // The body colours activity, not health: "is this process up?" is what the
  // dot below and the health rail already answer, and it left every working
  // stage looking identical to every other one.
  const color = activityColor(ratio, pulse);
  const rgb = activityRgb(ratio, pulse);
  const lit = ratio > 0;
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
      title={
        `${stage.label} — ${HEALTH_LABEL[stage.status]} · ${fmtRate(stage.throughput_per_min)}` +
        (lit ? ` · ${Math.round(ratio * 100)}% de l’étape la plus active` : " · aucune activité")
      }
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
          background: `linear-gradient(160deg, ${rgba(rgb, 0.10 + ratio * 0.22)}, rgba(255,255,255,0.02))`,
          border: `1px solid ${rgba(rgb, lit ? 0.30 + ratio * 0.45 : 0.22)}`,
          // The glow is the live half of the signal: it swells as an event
          // lands and fades with the pulse, so a busy stage visibly flickers
          // while a quiet one just sits at its counted colour.
          boxShadow: `0 0 ${8 + pulse * 26}px ${rgba(rgb, pulse * 0.5)}, 0 8px 24px rgba(0,0,0,0.35)`,
          transition: 'background .25s ease, border-color .25s ease, transform .16s ease',
          '&:hover': {
            borderColor: rgba(rgb, 0.85),
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
  const pulses = useStagePulses();
  // Intensity is relative to the busiest stage in this same snapshot, so the
  // row answers "where is the work happening" rather than "how big are these
  // numbers in the abstract".
  const busiest = busiestVolume(stages);
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
          <Node
            stage={stage}
            index={i}
            now={now}
            ratio={activityRatio(stage.volume, busiest)}
            pulse={pulses[stage.id] ?? 0}
            onSelect={onSelect}
          />
          {i < stages.length - 1 && <Connector nextStage={stages[i + 1]} />}
        </Box>
      ))}
    </Box>
  );
}
