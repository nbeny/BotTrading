'use client';

import { useCallback, useEffect, useState } from 'react';
import {
  Alert,
  Box,
  Card,
  CardContent,
  Chip,
  Divider,
  FormControlLabel,
  Stack,
  Switch,
  Typography,
} from '@mui/material';
import PodcastsIcon from '@mui/icons-material/Podcasts';
import { collectorsApi, type AiQuotaStatus, type CollectorRuntime } from '@/lib/api/endpoints';
import { apiErrorMessage } from '@/lib/api/client';

const LABELS: Record<string, string> = {
  bluesky: 'Bluesky',
  reddit: 'Reddit',
  mastodon: 'Mastodon',
  fourchan: '4chan',
  neynar: 'Farcaster',
  youtube: 'YouTube',
  lens: 'Lens',
  telegram: 'Telegram',
  cryptocompare: 'CryptoCompare',
  gdelt: 'GDELT',
  newsdata: 'NewsData',
  rss: 'RSS',
};

function QuotaBadge({ quota }: { quota: AiQuotaStatus | null }) {
  if (!quota) return null;
  if (!quota.paused) {
    return <Chip size="small" color="success" variant="outlined" label="IA active" />;
  }
  const when = quota.resume_at
    ? new Date(quota.resume_at * 1000).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })
    : '?';
  return <Chip size="small" color="warning" label={`IA en pause jusqu'à ${when}`} />;
}

export function SourcesPanel() {
  const [rt, setRt] = useState<CollectorRuntime | null>(null);
  const [quota, setQuota] = useState<AiQuotaStatus | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const refresh = useCallback(async () => {
    try {
      const [r, q] = await Promise.all([collectorsApi.runtime(), collectorsApi.aiQuota()]);
      setRt(r);
      setQuota(q);
      setError(null);
    } catch (e) {
      setError(apiErrorMessage(e, 'Impossible de charger les sources'));
    }
  }, []);

  useEffect(() => {
    refresh();
    const id = setInterval(refresh, 30_000);
    return () => clearInterval(id);
  }, [refresh]);

  const patch = useCallback(
    async (body: Parameters<typeof collectorsApi.setRuntime>[0]) => {
      setBusy(true);
      try {
        setRt(await collectorsApi.setRuntime(body));
        setError(null);
      } catch (e) {
        setError(apiErrorMessage(e, 'Échec de la mise à jour'));
      } finally {
        setBusy(false);
      }
    },
    [],
  );

  const category = (kind: 'social' | 'news') => {
    if (!rt) return null;
    const enabled = kind === 'social' ? rt.social_enabled : rt.news_enabled;
    const platforms = rt.known_platforms[kind] ?? [];
    return (
      <Box>
        <FormControlLabel
          control={
            <Switch
              checked={enabled}
              disabled={busy}
              onChange={(e) =>
                patch(kind === 'social' ? { social_enabled: e.target.checked } : { news_enabled: e.target.checked })
              }
            />
          }
          label={
            <Typography sx={{ fontWeight: 700 }}>
              {kind === 'social' ? 'Social' : 'News'}
            </Typography>
          }
        />
        <Stack direction="row" flexWrap="wrap" useFlexGap spacing={1} sx={{ pl: 4, opacity: enabled ? 1 : 0.4 }}>
          {platforms.map((p) => (
            <FormControlLabel
              key={p}
              sx={{ minWidth: 150 }}
              control={
                <Switch
                  size="small"
                  checked={rt.platforms[p] ?? true}
                  disabled={busy || !enabled}
                  onChange={(e) => patch({ platforms: { [p]: e.target.checked } })}
                />
              }
              label={LABELS[p] ?? p}
            />
          ))}
        </Stack>
      </Box>
    );
  };

  return (
    <Card>
      <CardContent>
        <Stack direction="row" alignItems="center" justifyContent="space-between" sx={{ mb: 0.5 }}>
          <Stack direction="row" spacing={1} alignItems="center">
            <PodcastsIcon color="primary" />
            <Typography variant="h6" sx={{ fontWeight: 800 }}>
              Sources de données
            </Typography>
          </Stack>
          <QuotaBadge quota={quota} />
        </Stack>
        <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>
          Active ou coupe la collecte par catégorie et par plateforme. Tout est activé par défaut.
        </Typography>

        {error && <Alert severity="error" sx={{ mb: 2 }}>{error}</Alert>}

        {!rt ? (
          <Typography variant="body2" color="text.secondary">Chargement…</Typography>
        ) : (
          <Stack spacing={2} divider={<Divider flexItem />}>
            {category('social')}
            {category('news')}
          </Stack>
        )}
      </CardContent>
    </Card>
  );
}
