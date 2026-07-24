'use client';

import { Box, Chip, Stack, Typography } from '@mui/material';
import PsychologyIcon from '@mui/icons-material/Psychology';
import { HealthDot, MiniBar, Stat } from './common';
import type { AiWorker } from '@/lib/types/systems';

function fmtTok(n: number) {
  if (n >= 1e6) return `${(n / 1e6).toFixed(1)}M`;
  if (n >= 1e3) return `${(n / 1e3).toFixed(0)}k`;
  return `${n}`;
}

export function AiWorkersPanel({ workers }: { workers: AiWorker[] }) {
  return (
    <Stack spacing={1.5}>
      {workers.map((w) => {
        const accent = w.tier === 'triage' ? '#4d9fff' : '#a78bfa';
        return (
          <Box
            key={w.name}
            sx={{
              p: 2,
              borderRadius: 3,
              border: '1px solid',
              borderColor: 'divider',
              background: `linear-gradient(160deg, ${accent}12, rgba(255,255,255,0.006))`,
            }}
          >
            <Stack direction="row" justifyContent="space-between" alignItems="center" sx={{ mb: 1.5 }}>
              <Stack direction="row" spacing={1.25} alignItems="center">
                <Box sx={{ width: 32, height: 32, borderRadius: 2, display: 'grid', placeItems: 'center', bgcolor: `${accent}22`, color: accent }}>
                  <PsychologyIcon sx={{ fontSize: 19 }} />
                </Box>
                <Box>
                  <Typography sx={{ fontWeight: 700, fontSize: 14, lineHeight: 1.1 }}>{w.name}</Typography>
                  <Typography variant="caption" color="text.secondary" className="mono">
                    {w.model}
                  </Typography>
                </Box>
              </Stack>
              <Stack direction="row" spacing={1} alignItems="center">
                <HealthDot status={w.status} size={8} />
                <Chip
                  label={w.tier === 'triage' ? 'Triage' : 'Senior'}
                  size="small"
                  sx={{ height: 20, fontSize: 10.5, bgcolor: `${accent}1f`, color: accent }}
                />
              </Stack>
            </Stack>

            <Box sx={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 1.5, mb: 1.5 }}>
              <Stat label="Requêtes / h" value={w.requests_last_hour.toLocaleString('fr-FR')} />
              <Stat label="Coût (jour)" value={`$${w.cost_usd_today.toFixed(2)}`} />
              <Stat label="Latence" value={`${(w.avg_latency_ms / 1000).toFixed(1)}s`} />
            </Box>

            <Stack direction="row" spacing={2} sx={{ mb: 1.25 }}>
              <Typography variant="caption" color="text.secondary">
                Tokens <span className="mono" style={{ color: '#e8ecf5' }}>{fmtTok(w.tokens_in)}</span> in ·{' '}
                <span className="mono" style={{ color: '#e8ecf5' }}>{fmtTok(w.tokens_out)}</span> out
              </Typography>
              <Typography variant="caption" color="text.secondary">
                File d’attente <span className="mono" style={{ color: '#e8ecf5' }}>{w.queue_depth}</span>
              </Typography>
            </Stack>

            <Box>
              <Stack direction="row" justifyContent="space-between" sx={{ mb: 0.4 }}>
                <Typography variant="caption" color="text.secondary">
                  Taux d’escalade
                </Typography>
                <Typography variant="caption" className="mono" sx={{ color: accent, fontWeight: 700 }}>
                  {Math.round(w.escalation_rate * 100)}%
                </Typography>
              </Stack>
              <MiniBar pct={w.escalation_rate * 100} color={accent} height={5} />
            </Box>
          </Box>
        );
      })}
    </Stack>
  );
}
