'use client';

import {
  Box,
  Button,
  Card,
  CardContent,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogContentText,
  DialogTitle,
  FormControlLabel,
  Stack,
  Switch,
  ToggleButton,
  ToggleButtonGroup,
  Tooltip,
  Typography,
} from '@mui/material';
import PowerSettingsNewIcon from '@mui/icons-material/PowerSettingsNew';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { settingsApi } from '@/lib/api/endpoints';
import { useAuth } from '@/lib/auth/AuthProvider';
import { apiErrorMessage } from '@/lib/api/client';
import type { TradingMode } from '@/lib/types/domain';

interface Props {
  onSuccess: (msg: string) => void;
  onError: (msg: string) => void;
}

const MODES: { value: TradingMode; label: string }[] = [
  { value: 'dry_run', label: 'Dry Run' },
  { value: 'demo', label: 'Demo' },
  { value: 'live', label: 'Live' },
];

export function EngineControlCard({ onSuccess, onError }: Props) {
  const { can } = useAuth();
  const qc = useQueryClient();
  const [liveConfirmOpen, setLiveConfirmOpen] = useState(false);
  // Double-confirm: the second checkbox-style acknowledgement inside the dialog.
  const [liveAcknowledged, setLiveAcknowledged] = useState(false);

  const { data: status, isLoading } = useQuery({
    queryKey: ['trading', 'status'],
    queryFn: settingsApi.status,
    refetchInterval: 10_000,
  });

  const invalidate = () => qc.invalidateQueries({ queryKey: ['trading', 'status'] });

  const toggleAutoMutation = useMutation({
    mutationFn: (enabled: boolean) => settingsApi.setAuto(enabled),
    onSuccess: () => {
      invalidate();
      onSuccess('Trading automatique mis à jour');
    },
    onError: (err) => onError(apiErrorMessage(err, 'Erreur lors de la mise à jour')),
  });

  const toggleKillMutation = useMutation({
    mutationFn: (enabled: boolean) => settingsApi.setKill(enabled),
    onSuccess: () => {
      invalidate();
      onSuccess('Kill-switch mis à jour');
    },
    onError: (err) => onError(apiErrorMessage(err, 'Erreur lors de la mise à jour du kill-switch')),
  });

  const setModeMutation = useMutation({
    mutationFn: (mode: TradingMode) => settingsApi.setMode(mode),
    onSuccess: (_data, mode) => {
      invalidate();
      onSuccess(`Mode basculé vers ${mode.toUpperCase()}`);
      closeLiveDialog();
    },
    onError: (err) => {
      onError(apiErrorMessage(err, 'Erreur lors du changement de mode'));
      closeLiveDialog();
    },
  });

  const canToggleAuto = can('trading.toggle_auto');
  const canSwitchMode = can('trading.switch_mode');
  // Kill-switch is engaged when trading is disabled.
  const killEngaged = status ? !status.trading_enabled : false;

  function closeLiveDialog() {
    setLiveConfirmOpen(false);
    setLiveAcknowledged(false);
  }

  function handleAutoToggle(checked: boolean) {
    if (!canToggleAuto) return;
    toggleAutoMutation.mutate(checked);
  }

  function handleKillToggle(checked: boolean) {
    if (!canSwitchMode) return;
    toggleKillMutation.mutate(checked);
  }

  function handleModeChange(next: TradingMode | null) {
    if (!next || !canSwitchMode || !status || next === status.mode) return;
    if (next === 'live') {
      setLiveConfirmOpen(true);
    } else {
      setModeMutation.mutate(next);
    }
  }

  function handleLiveConfirm() {
    if (!liveAcknowledged) return;
    setModeMutation.mutate('live');
  }

  return (
    <>
      <Card sx={{ height: '100%' }}>
        <CardContent>
          <Stack direction="row" alignItems="center" spacing={1} sx={{ mb: 2 }}>
            <PowerSettingsNewIcon color="primary" />
            <Typography variant="h6">Contrôle du moteur</Typography>
          </Stack>

          {isLoading ? (
            <Box sx={{ display: 'flex', justifyContent: 'center', py: 3 }}>
              <CircularProgress size={32} />
            </Box>
          ) : (
            <Stack spacing={3}>
              {/* Auto trading toggle */}
              <Box>
                <Typography variant="caption" color="text.secondary" sx={{ textTransform: 'uppercase', letterSpacing: 0.6 }}>
                  Trading automatique
                </Typography>
                <Box sx={{ mt: 1 }}>
                  <Tooltip
                    title={!canToggleAuto ? 'Permission requise' : ''}
                    disableHoverListener={canToggleAuto}
                  >
                    <span>
                      <FormControlLabel
                        control={
                          <Switch
                            checked={status?.auto_trading_enabled ?? false}
                            onChange={(_, checked) => handleAutoToggle(checked)}
                            disabled={!canToggleAuto || toggleAutoMutation.isPending}
                            color="success"
                          />
                        }
                        label={
                          <Typography variant="body2" sx={{ fontWeight: 600 }}>
                            {status?.auto_trading_enabled ? 'Activé' : 'Désactivé'}
                          </Typography>
                        }
                      />
                    </span>
                  </Tooltip>
                </Box>
              </Box>

              {/* Kill switch */}
              <Box>
                <Typography variant="caption" color="text.secondary" sx={{ textTransform: 'uppercase', letterSpacing: 0.6 }}>
                  Kill-switch
                </Typography>
                <Box sx={{ mt: 1 }}>
                  <Tooltip
                    title={!canSwitchMode ? 'Permission requise' : 'Coupe immédiatement toute prise d\'ordre'}
                    disableHoverListener={false}
                  >
                    <span>
                      <FormControlLabel
                        control={
                          <Switch
                            checked={killEngaged}
                            onChange={(_, checked) => handleKillToggle(checked)}
                            disabled={!canSwitchMode || toggleKillMutation.isPending}
                            color="error"
                          />
                        }
                        label={
                          <Typography variant="body2" sx={{ fontWeight: 600 }}>
                            {killEngaged ? 'Engagé (trading coupé)' : 'Relâché'}
                          </Typography>
                        }
                      />
                    </span>
                  </Tooltip>
                </Box>
              </Box>

              {/* Trading mode — 3-way selector */}
              <Box>
                <Typography variant="caption" color="text.secondary" sx={{ textTransform: 'uppercase', letterSpacing: 0.6 }}>
                  Mode de trading
                </Typography>
                <Box sx={{ mt: 1 }}>
                  <Tooltip
                    title={!canSwitchMode ? 'Permission requise' : ''}
                    disableHoverListener={canSwitchMode}
                  >
                    <span>
                      <ToggleButtonGroup
                        exclusive
                        size="small"
                        value={status?.mode ?? null}
                        onChange={(_, v) => handleModeChange(v as TradingMode | null)}
                        disabled={!canSwitchMode || setModeMutation.isPending}
                      >
                        {MODES.map((m) => (
                          <ToggleButton
                            key={m.value}
                            value={m.value}
                            color={m.value === 'live' ? 'warning' : 'primary'}
                            sx={{ px: 2, fontWeight: 700, letterSpacing: 0.5 }}
                          >
                            {m.label}
                          </ToggleButton>
                        ))}
                      </ToggleButtonGroup>
                    </span>
                  </Tooltip>
                </Box>
              </Box>
            </Stack>
          )}
        </CardContent>
      </Card>

      {/* LIVE confirmation dialog — double confirm */}
      <Dialog open={liveConfirmOpen} onClose={closeLiveDialog} maxWidth="sm" fullWidth>
        <DialogTitle sx={{ color: 'warning.main' }}>⚠ Activation du mode LIVE</DialogTitle>
        <DialogContent>
          <DialogContentText>
            Vous êtes sur le point de basculer en mode <strong>LIVE</strong>. Cette action activera
            les ordres réels sur <strong>Kraken</strong>. Des fonds réels seront engagés.
          </DialogContentText>
          <FormControlLabel
            sx={{ mt: 2 }}
            control={
              <Switch
                checked={liveAcknowledged}
                onChange={(_, checked) => setLiveAcknowledged(checked)}
                color="warning"
              />
            }
            label={
              <Typography variant="body2" sx={{ fontWeight: 600 }}>
                Je confirme engager des fonds réels
              </Typography>
            }
          />
        </DialogContent>
        <DialogActions>
          <Button onClick={closeLiveDialog} color="inherit">
            Annuler
          </Button>
          <Button
            onClick={handleLiveConfirm}
            color="warning"
            variant="contained"
            disabled={!liveAcknowledged || setModeMutation.isPending}
          >
            {setModeMutation.isPending ? <CircularProgress size={18} /> : 'Confirmer — LIVE'}
          </Button>
        </DialogActions>
      </Dialog>
    </>
  );
}
