'use client';

import { Box, Stack, Tooltip, Typography } from '@mui/material';
import { HEALTH_COLOR, HealthDot } from './common';
import { HEALTH_LABEL, type PipelineStage } from '@/lib/types/systems';

function Connector({ throughput }: { throughput: number }) {
  return (
    <Box
      sx={{
        position: 'relative',
        flex: '0 0 auto',
        width: { xs: 54, md: 72 },
        height: 96,
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
          top: 6,
          fontSize: 10,
          color: 'text.secondary',
          whiteSpace: 'nowrap',
        }}
      >
        {Math.round(throughput)}/m
      </Typography>
    </Box>
  );
}

function Node({ stage, index }: { stage: PipelineStage; index: number }) {
  const color = HEALTH_COLOR[stage.status];
  return (
    <Tooltip title={`${stage.label} — ${HEALTH_LABEL[stage.status]} · ${Math.round(stage.throughput_per_min)} evt/min`}>
      <Box
        className="reveal"
        sx={{
          ['--d' as string]: `${index * 70}ms`,
          flex: '0 0 auto',
          width: { xs: 116, md: 132 },
          height: 96,
          borderRadius: 3,
          p: 1.5,
          display: 'flex',
          flexDirection: 'column',
          justifyContent: 'space-between',
          position: 'relative',
          background: `linear-gradient(160deg, ${color}1a, rgba(255,255,255,0.02))`,
          border: `1px solid ${color}40`,
          boxShadow: `0 0 0 1px ${color}10, 0 8px 24px rgba(0,0,0,0.35)`,
        }}
      >
        <Stack direction="row" alignItems="center" spacing={0.75}>
          <HealthDot status={stage.status} size={8} />
          <Typography variant="caption" sx={{ fontWeight: 700, fontSize: 10.5, color: 'text.secondary' }}>
            {String(index + 1).padStart(2, '0')}
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
      </Box>
    </Tooltip>
  );
}

export function PipelineFlow({ stages }: { stages: PipelineStage[] }) {
  return (
    <Box
      sx={{
        display: 'flex',
        alignItems: 'center',
        overflowX: 'auto',
        pb: 1,
        // hide native scrollbar chrome but keep scroll
        '&::-webkit-scrollbar': { height: 6 },
      }}
    >
      {stages.map((stage, i) => (
        <Box key={stage.id} sx={{ display: 'flex', alignItems: 'center' }}>
          <Node stage={stage} index={i} />
          {i < stages.length - 1 && <Connector throughput={stages[i + 1].throughput_per_min} />}
        </Box>
      ))}
    </Box>
  );
}
