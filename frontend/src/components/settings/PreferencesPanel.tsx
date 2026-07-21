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
  Tooltip,
  Typography,
} from '@mui/material';
import SaveIcon from '@mui/icons-material/Save';
import InfoIcon from '@mui/icons-material/Info';
import { useAuth } from '@/lib/auth/AuthProvider';

interface Prefs {
  soundOnAlerts: boolean;
  tableDense: boolean;
  showPnlInHeader: boolean;
  autoRefreshCharts: boolean;
}

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

  function toggle(key: keyof Prefs) {
    setPrefs((p) => ({ ...p, [key]: !p[key] }));
    setSaved(false);
  }

  function handleSave() {
    // Cosmetic only — state is local, no persistence
    setSaved(true);
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
      </CardContent>
    </Card>
  );
}
