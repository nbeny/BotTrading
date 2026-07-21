'use client';

import { useState } from 'react';
import {
  Box,
  Button,
  Card,
  CardContent,
  Divider,
  FormControlLabel,
  Stack,
  Switch,
  TextField,
  Tooltip,
  Typography,
} from '@mui/material';
import SaveIcon from '@mui/icons-material/Save';
import InfoIcon from '@mui/icons-material/Info';
import TuneIcon from '@mui/icons-material/Tune';
import { useMutation } from '@tanstack/react-query';
import { useAuth } from '@/lib/auth/AuthProvider';
import { settingsApi } from '@/lib/api/endpoints';
import { apiErrorMessage } from '@/lib/api/client';
import type { EngineCaps } from '@/lib/types/domain';

interface Prefs {
  soundOnAlerts: boolean;
  tableDense: boolean;
  showPnlInHeader: boolean;
  autoRefreshCharts: boolean;
}

const CAP_FIELDS: { key: keyof EngineCaps; label: string; helper: string }[] = [
  { key: 'max_order_usd', label: 'Ordre max (USD)', helper: 'Notionnel maximum par ordre' },
  { key: 'max_leverage', label: 'Levier max', helper: 'Levier maximum autorisé' },
  { key: 'max_orders_per_hour', label: 'Ordres / heure', helper: 'Débit maximum d\'ordres' },
  { key: 'entry_timeout_s', label: 'Timeout entrée (s)', helper: 'Délai avant annulation d\'entrée' },
  { key: 'reconcile_interval_s', label: 'Intervalle réconciliation (s)', helper: 'Période de réconciliation' },
];

const DEFAULT_CAPS: EngineCaps = {
  max_order_usd: 1000,
  max_leverage: 3,
  max_orders_per_hour: 20,
  entry_timeout_s: 60,
  reconcile_interval_s: 30,
};

export function PreferencesPanel() {
  const { can } = useAuth();
  const canEdit = can('settings.edit');

  const [prefs, setPrefs] = useState<Prefs>({
    soundOnAlerts: false,
    tableDense: true,
    showPnlInHeader: true,
    autoRefreshCharts: true,
  });
  const [saved, setSaved] = useState(false);

  const [caps, setCaps] = useState<EngineCaps>(DEFAULT_CAPS);
  const [capsSaved, setCapsSaved] = useState(false);

  const setCapsMutation = useMutation({
    mutationFn: (payload: Partial<EngineCaps>) => settingsApi.setCaps(payload),
    onSuccess: () => setCapsSaved(true),
    onError: () => setCapsSaved(false),
  });

  function toggle(key: keyof Prefs) {
    setPrefs((p) => ({ ...p, [key]: !p[key] }));
    setSaved(false);
  }

  function handleSave() {
    // Cosmetic only — state is local, no persistence
    setSaved(true);
  }

  function handleCapChange(key: keyof EngineCaps, raw: string) {
    const value = Number(raw);
    setCaps((c) => ({ ...c, [key]: Number.isFinite(value) ? value : 0 }));
    setCapsSaved(false);
  }

  function handleCapsSave() {
    setCapsMutation.mutate(caps);
  }

  return (
    <Card>
      <CardContent>
        <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 0.5 }}>
          <Typography variant="subtitle1" fontWeight={700}>
            Préférences
          </Typography>
          <Tooltip title="Ces préférences sont stockées en mémoire locale et non persistées entre sessions.">
            <InfoIcon sx={{ fontSize: 15, color: 'text.disabled' }} />
          </Tooltip>
        </Stack>
        <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 2 }}>
          Paramètres d&apos;interface non persistés (à titre d&apos;exemple).
        </Typography>

        <Stack spacing={1} divider={<Divider />}>
          <FormControlLabel
            control={
              <Switch
                checked={prefs.soundOnAlerts}
                onChange={() => toggle('soundOnAlerts')}
                disabled={!canEdit}
                size="small"
              />
            }
            label={
              <Box>
                <Typography variant="body2" fontWeight={600}>Son sur alertes</Typography>
                <Typography variant="caption" color="text.secondary">
                  Jouer un son lors d&apos;une alerte risque critique
                </Typography>
              </Box>
            }
            labelPlacement="start"
            sx={{ justifyContent: 'space-between', ml: 0, mr: 0 }}
          />
          <FormControlLabel
            control={
              <Switch
                checked={prefs.tableDense}
                onChange={() => toggle('tableDense')}
                disabled={!canEdit}
                size="small"
              />
            }
            label={
              <Box>
                <Typography variant="body2" fontWeight={600}>Densité tableau élevée</Typography>
                <Typography variant="caption" color="text.secondary">
                  Affichage compact des tableaux de données
                </Typography>
              </Box>
            }
            labelPlacement="start"
            sx={{ justifyContent: 'space-between', ml: 0, mr: 0 }}
          />
          <FormControlLabel
            control={
              <Switch
                checked={prefs.showPnlInHeader}
                onChange={() => toggle('showPnlInHeader')}
                disabled={!canEdit}
                size="small"
              />
            }
            label={
              <Box>
                <Typography variant="body2" fontWeight={600}>PnL dans l&apos;en-tête</Typography>
                <Typography variant="caption" color="text.secondary">
                  Afficher le PnL journalier dans la barre de navigation
                </Typography>
              </Box>
            }
            labelPlacement="start"
            sx={{ justifyContent: 'space-between', ml: 0, mr: 0 }}
          />
          <FormControlLabel
            control={
              <Switch
                checked={prefs.autoRefreshCharts}
                onChange={() => toggle('autoRefreshCharts')}
                disabled={!canEdit}
                size="small"
              />
            }
            label={
              <Box>
                <Typography variant="body2" fontWeight={600}>Actualisation auto des graphiques</Typography>
                <Typography variant="caption" color="text.secondary">
                  Mettre à jour les graphiques toutes les 30 secondes
                </Typography>
              </Box>
            }
            labelPlacement="start"
            sx={{ justifyContent: 'space-between', ml: 0, mr: 0 }}
          />
        </Stack>

        <Box sx={{ mt: 2.5, display: 'flex', justifyContent: 'flex-end', gap: 1, alignItems: 'center' }}>
          {saved && (
            <Typography variant="caption" color="success.main">
              Préférences sauvegardées (session uniquement)
            </Typography>
          )}
          <Tooltip title={!canEdit ? 'Permission requise : settings.edit' : ''}>
            <span>
              <Button
                variant="contained"
                size="small"
                startIcon={<SaveIcon />}
                disabled={!canEdit}
                onClick={handleSave}
              >
                Sauvegarder
              </Button>
            </span>
          </Tooltip>
        </Box>

        <Divider sx={{ my: 3 }} />

        {/* Engine caps ─────────────────────────────────────────────── */}
        <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 0.5 }}>
          <TuneIcon sx={{ fontSize: 18, color: 'primary.main' }} />
          <Typography variant="subtitle1" fontWeight={700}>
            Limites du moteur
          </Typography>
        </Stack>
        <Typography variant="caption" color="text.secondary" sx={{ display: 'block', mb: 2 }}>
          Plafonds de risque appliqués par le moteur de trading.
        </Typography>

        <Box
          sx={{
            display: 'grid',
            gap: 2,
            gridTemplateColumns: { xs: '1fr', sm: '1fr 1fr' },
          }}
        >
          {CAP_FIELDS.map((f) => (
            <TextField
              key={f.key}
              type="number"
              size="small"
              label={f.label}
              helperText={f.helper}
              value={caps[f.key]}
              onChange={(e) => handleCapChange(f.key, e.target.value)}
              disabled={!canEdit || setCapsMutation.isPending}
              slotProps={{ htmlInput: { min: 0 } }}
            />
          ))}
        </Box>

        <Box sx={{ mt: 2.5, display: 'flex', justifyContent: 'flex-end', gap: 1, alignItems: 'center' }}>
          {capsSaved && (
            <Typography variant="caption" color="success.main">
              Limites appliquées
            </Typography>
          )}
          {setCapsMutation.isError && (
            <Typography variant="caption" color="error.main">
              {apiErrorMessage(setCapsMutation.error, 'Échec de la mise à jour')}
            </Typography>
          )}
          <Tooltip title={!canEdit ? 'Permission requise : settings.edit' : ''}>
            <span>
              <Button
                variant="contained"
                size="small"
                startIcon={<SaveIcon />}
                disabled={!canEdit || setCapsMutation.isPending}
                onClick={handleCapsSave}
              >
                Appliquer les limites
              </Button>
            </span>
          </Tooltip>
        </Box>
      </CardContent>
    </Card>
  );
}
