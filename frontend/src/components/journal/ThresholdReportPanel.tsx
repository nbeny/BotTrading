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

export function ThresholdReportPanel() {
  const [now, setNow] = useState(() => Date.now());
  const queryClient = useQueryClient();

  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), 30_000);
    return () => clearInterval(id);
  }, []);

  const { data } = useQuery({
    queryKey: ['journal', 'threshold'],
    queryFn: journalApi.threshold,
    refetchInterval: (q) => (q.state.data?.running ? 5_000 : 60_000),
  });

  const running = data?.running ?? false;

  async function relancer() {
    await analysisApi.requestThresholdScan();
    await queryClient.invalidateQueries({ queryKey: ['journal', 'threshold'] });
  }

  const header = (
    <Stack direction="row" spacing={2} alignItems="center" justifyContent="space-between" sx={{ mb: 2 }}>
      <Stack direction="row" spacing={1.5} alignItems="center">
        <Typography variant="body2" sx={{ opacity: 0.7 }}>Dernier scan</Typography>
        <AgeLabel computedAt={data?.computed_at ?? null} now={now} />
        {running && (
          <Chip label="calcul en cours…" size="small" color="info" variant="outlined" />
        )}
      </Stack>
      <Button
        variant="outlined"
        size="small"
        startIcon={<ReplayIcon fontSize="small" />}
        disabled={running}
        onClick={relancer}
      >
        Relancer
      </Button>
    </Stack>
  );

  // Last scan failed — say so rather than showing a stale report as fresh.
  if (data && data.status === 'error') {
    return (
      <Box>
        {header}
        <Alert severity="error">
          Dernier scan échoué le {fmtRelative(data.computed_at, now)} : {data.error ?? '—'}
        </Alert>
      </Box>
    );
  }

  const report = data?.report ?? null;

  return (
    <Box>
      {header}
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
                {report.proposal.actual_per_day}/{report.proposal.target_per_day} décisions par jour ·{' '}
                {report.proposal.distinct_symbols} symboles distincts · {report.proposal.passing_pct.toFixed(1)}% passant
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
