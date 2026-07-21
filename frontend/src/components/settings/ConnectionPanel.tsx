'use client';

import {
  Box,
  Card,
  CardContent,
  Chip,
  Divider,
  Stack,
  Typography,
} from '@mui/material';
import CircleIcon from '@mui/icons-material/Circle';
import { useWebSocket } from '@/lib/ws/WebSocketProvider';
import { WS_URL, API_BASE, USE_MOCK } from '@/lib/config';
import type { ConnStatus } from '@/lib/ws/WebSocketProvider';

const STATUS_LABEL: Record<ConnStatus, string> = {
  connecting: 'Connexion...',
  open: 'Connecté',
  closed: 'Déconnecté',
};

const STATUS_COLOR: Record<ConnStatus, 'success' | 'warning' | 'error'> = {
  connecting: 'warning',
  open: 'success',
  closed: 'error',
};

function EndpointRow({ label, value }: { label: string; value: string }) {
  return (
    <Stack direction={{ xs: 'column', sm: 'row' }} spacing={1} alignItems={{ sm: 'center' }}>
      <Typography variant="caption" color="text.secondary" sx={{ minWidth: 120 }}>
        {label}
      </Typography>
      <Typography variant="caption" className="mono" sx={{ wordBreak: 'break-all' }}>
        {value}
      </Typography>
    </Stack>
  );
}

export function ConnectionPanel() {
  const { status, lastEventAt } = useWebSocket();

  return (
    <Card>
      <CardContent>
        <Typography variant="subtitle1" fontWeight={700} gutterBottom>
          Connexion temps réel &amp; API
        </Typography>

        {/* WS Status */}
        <Stack direction="row" spacing={1.5} alignItems="center" sx={{ mb: 2 }}>
          <CircleIcon
            sx={{
              fontSize: 10,
              color:
                status === 'open'
                  ? 'success.main'
                  : status === 'connecting'
                  ? 'warning.main'
                  : 'error.main',
            }}
          />
          <Typography variant="body2" fontWeight={600}>
            WebSocket: {STATUS_LABEL[status]}
          </Typography>
          <Chip
            label={status.toUpperCase()}
            size="small"
            color={STATUS_COLOR[status]}
            variant="outlined"
            sx={{ fontSize: 10, height: 18 }}
          />
        </Stack>

        {lastEventAt && (
          <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 2 }}>
            Dernier événement reçu :{' '}
            {new Date(lastEventAt).toLocaleTimeString('fr-FR')}
          </Typography>
        )}

        <Divider sx={{ mb: 2 }} />

        {/* Endpoints */}
        <Typography variant="caption" color="text.secondary" sx={{ textTransform: 'uppercase', letterSpacing: 0.6, mb: 1, display: 'block' }}>
          Points de terminaison (lecture seule)
        </Typography>
        <Stack spacing={1}>
          <EndpointRow label="REST API Base" value={API_BASE} />
          <EndpointRow label="WebSocket URL" value={WS_URL} />
        </Stack>

        <Divider sx={{ my: 2 }} />

        {/* Mock vs Live note */}
        <Stack direction="row" spacing={1} alignItems="center">
          <Chip
            label={USE_MOCK ? 'MODE MOCK' : 'MODE LIVE'}
            color={USE_MOCK ? 'warning' : 'success'}
            size="small"
            variant="filled"
          />
          <Typography variant="caption" color="text.secondary">
            {USE_MOCK
              ? 'Les données et le flux temps réel sont simulés (NEXT_PUBLIC_USE_MOCK=1). Aucun accès réel au marché.'
              : 'Connecté à l\'API réelle via le gateway Traefik. Les trades sont exécutés sur Kraken.'}
          </Typography>
        </Stack>
      </CardContent>
    </Card>
  );
}
