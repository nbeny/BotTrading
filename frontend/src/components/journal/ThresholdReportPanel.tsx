'use client';

import { useEffect, useState } from 'react';
import { useQuery, useQueryClient } from '@tanstack/react-query';
import {
  Alert,
  Box,
  Button,
  Chip,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  Typography,
} from '@mui/material';
import ReplayIcon from '@mui/icons-material/Replay';
import { analysisApi, journalApi } from '@/lib/api/endpoints';
import { fmtRelative } from '@/lib/format';
import { EmptyState } from '@/components/common';

/** Age past which the report reads as stale rather than fresh — the panel
 *  must still show it (it's the last thing we know), but must not pretend
 *  it's current. */
const STALE_AFTER_MS = 24 * 3_600_000;

function AgeLabel({ computedAt, now }: { computedAt: string | null; now: number }) {
  if (computedAt == null) return <Typography variant="caption" sx={{ opacity: 0.6 }}>—</Typography>;
  const stale = now - new Date(computedAt).getTime() > STALE_AFTER_MS;
  return (
    <Typography
      variant="caption"
      sx={{ opacity: stale ? 1 : 0.6, color: stale ? 'warning.main' : undefined, fontWeight: stale ? 700 : 400 }}
    >
      {fmtRelative(computedAt, now)}
      {stale ? ' — rapport ancien' : ''}
    </Typography>
  );
}

/** Ms after which a lost/dropped "Relancer" command stops blocking the
 *  button — the measured chain to `running: true` in Redis is 0.3–2 s, so
 *  20 s is generous slack, not the expected case. */
const PENDING_TIMEOUT_MS = 20_000;

export function ThresholdReportPanel() {
  const [now, setNow] = useState(() => Date.now());
  const [pending, setPending] = useState(false);
  const queryClient = useQueryClient();

  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 30_000);
    return () => clearInterval(id);
  }, []);

  const { data } = useQuery({
    queryKey: ['journal', 'threshold'],
    queryFn: journalApi.threshold,
    refetchInterval: (q) => (pending || q.state.data?.running ? 5_000 : 60_000),
  });

  const running = data?.running ?? false;

  // Redis confirms the scan started — the local "request sent" guess is no
  // longer needed, the real signal took over.
  useEffect(() => {
    if (running) setPending(false);
  }, [running]);

  // A command can be lost (Kafka hiccup, consumer down) — don't disable the
  // button forever waiting for a signal that will never come.
  useEffect(() => {
    if (!pending) return;
    const id = setTimeout(() => setPending(false), PENDING_TIMEOUT_MS);
    return () => clearTimeout(id);
  }, [pending]);

  async function relancer() {
    setPending(true);
    await analysisApi.requestThresholdScan();
    await queryClient.invalidateQueries({ queryKey: ['journal', 'threshold'] });
  }

  const header = (
    <Stack direction="row" spacing={2} alignItems="center" justifyContent="space-between" sx={{ mb: 2 }}>
      <Stack direction="row" spacing={1.5} alignItems="center">
        <Typography variant="body2" sx={{ opacity: 0.7 }}>Dernier rapport</Typography>
        <AgeLabel computedAt={data?.report_computed_at ?? null} now={now} />
        {running ? (
          <Chip label="calcul en cours…" size="small" color="info" variant="outlined" />
        ) : pending ? (
          <Chip label="demande envoyée…" size="small" color="info" variant="outlined" />
        ) : null}
      </Stack>
      <Button
        variant="outlined"
        size="small"
        startIcon={<ReplayIcon fontSize="small" />}
        disabled={running || pending}
        onClick={relancer}
      >
        Relancer
      </Button>
    </Stack>
  );

  const report = data?.report ?? null;

  return (
    <Box>
      {header}
      {/* The newest attempt can fail while a still-valid earlier report
       *  exists — say so without hiding that report (it's the last thing
       *  we know), never an early return. */}
      {data && data.status === 'error' && (
        <Alert severity="error" sx={{ mb: 2 }}>
          Dernier scan échoué le {fmtRelative(data.computed_at, now)} : {data.error ?? '—'}
        </Alert>
      )}
      {report === null ? (
        // Either the query hasn't landed yet, or no scan has ever run —
        // both render the same honest "nothing to show" state.
        <EmptyState message="Aucun scan encore effectué." />
      ) : (
        <Stack spacing={2}>
          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell>Axe</TableCell>
                <TableCell align="right">Poids</TableCell>
                <TableCell align="right">Présence</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {report.axes.map((axis) => (
                <TableRow key={axis.key}>
                  <TableCell>
                    {axis.key}
                    {axis.mute && (
                      <Chip label="muet" size="small" color="error" variant="outlined" sx={{ ml: 1, height: 18 }} />
                    )}
                  </TableCell>
                  <TableCell align="right">{axis.weight.toFixed(4)}</TableCell>
                  <TableCell align="right">
                    {axis.pct.toFixed(1)}% ({axis.seen})
                  </TableCell>
                </TableRow>
              ))}
            </TableBody>
          </Table>

          {report.refusal ? (
            <Alert severity="warning" data-testid="threshold-refusal">
              <Typography variant="subtitle2" fontWeight={700}>{report.refusal.title}</Typography>
              <Typography variant="body2" sx={{ whiteSpace: 'pre-line', mt: 1 }}>
                {report.refusal.detail}
              </Typography>
            </Alert>
          ) : report.proposal ? (
            <Alert severity="success" data-testid="threshold-proposal">
              <Typography variant="subtitle2" fontWeight={700}>
                Seuil proposé : {report.proposal.threshold}
              </Typography>
              <Typography variant="body2">
                {report.proposal.actual_per_day.toFixed(1)}/{report.proposal.target_per_day} décisions par jour ·{' '}
                {report.proposal.distinct_symbols.toFixed(1)} symboles distincts · {report.proposal.passing_pct.toFixed(1)}% passant
              </Typography>
            </Alert>
          ) : (
            <Typography variant="body2" sx={{ opacity: 0.6 }}>—</Typography>
          )}

          {report.warnings.length > 0 && (
            <Stack spacing={0.5}>
              {report.warnings.map((w, i) => (
                <Typography key={i} variant="caption" sx={{ opacity: 0.7, display: 'block' }}>
                  {w.text}
                </Typography>
              ))}
            </Stack>
          )}

          <Box>
            <Typography variant="caption" sx={{ opacity: 0.6 }}>Répartition par jour</Typography>
            <Stack direction="row" spacing={1} flexWrap="wrap" sx={{ mt: 0.5 }}>
              {Object.entries(report.window.by_day).map(([day, count]) => (
                <Chip key={day} label={`${day} : ${count}`} size="small" variant="outlined" />
              ))}
            </Stack>
          </Box>
        </Stack>
      )}
    </Box>
  );
}
