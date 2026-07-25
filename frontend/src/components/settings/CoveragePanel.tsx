'use client';

import { useCallback, useEffect, useState } from 'react';
import {
  Alert,
  Box,
  Button,
  Card,
  CardContent,
  LinearProgress,
  Stack,
  Typography,
} from '@mui/material';
import ReplayIcon from '@mui/icons-material/Replay';
import { collectorsApi, type Coverage } from '@/lib/api/endpoints';
import { apiErrorMessage } from '@/lib/api/client';

function Bar({ label, done, total }: { label: string; done: number; total: number }) {
  const pct = total > 0 ? Math.round((done / total) * 100) : 100;
  return (
    <Box>
      <Stack direction="row" justifyContent="space-between" sx={{ mb: 0.5 }}>
        <Typography variant="body2">{label}</Typography>
        <Typography variant="body2" color="text.secondary">
          {done}/{total} ({pct}%)
        </Typography>
      </Stack>
      <LinearProgress variant="determinate" value={pct} sx={{ height: 8, borderRadius: 1 }} />
    </Box>
  );
}

export function CoveragePanel() {
  const [cov, setCov] = useState<Coverage | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [msg, setMsg] = useState<string | null>(null);

  const refresh = useCallback(async () => {
    try {
      setCov(await collectorsApi.coverage());
      setError(null);
    } catch (e) {
      setError(apiErrorMessage(e, 'Impossible de charger la couverture'));
    }
  }, []);

  useEffect(() => {
    refresh();
    const id = setInterval(refresh, 30_000);
    return () => clearInterval(id);
  }, [refresh]);

  const runBackfill = useCallback(async () => {
    setBusy(true);
    setMsg(null);
    try {
      const r = await collectorsApi.backfill();
      setMsg(`${r.requeued} opportunité(s) renvoyée(s) à l'analyse senior.`);
      await refresh();
    } catch (e) {
      setError(apiErrorMessage(e, 'Échec de la repasse'));
    } finally {
      setBusy(false);
    }
  }, [refresh]);

  const pending = cov?.escalations.pending ?? 0;

  return (
    <Card>
      <CardContent>
        <Typography variant="h6" sx={{ fontWeight: 800, mb: 0.5 }}>
          Couverture & repasse
        </Typography>
        <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
          Vérifie que sentiment et décisions sont bien passés partout. La repasse
          renvoie les opportunités escaladées mais non décidées (coupure quota/source).
        </Typography>

        {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}
        {msg && <Alert severity="success" sx={{ mb: 2 }} onClose={() => setMsg(null)}>{msg}</Alert>}

        {!cov ? (
          <Typography variant="body2" color="text.secondary">Chargement…</Typography>
        ) : (
          <Stack spacing={2}>
            <Bar label="Sentiment scoré" done={cov.sentiment.scored} total={cov.sentiment.total} />
            <Stack direction="row" alignItems="center" justifyContent="space-between">
              <Typography variant="body2">
                Escalades en attente de décision : <b>{pending}</b>
              </Typography>
              <Button
                variant="contained"
                size="small"
                startIcon={<ReplayIcon />}
                disabled={busy || pending === 0}
                onClick={runBackfill}
              >
                {busy ? 'Repasse…' : 'Lancer la repasse'}
              </Button>
            </Stack>
          </Stack>
        )}
      </CardContent>
    </Card>
  );
}
