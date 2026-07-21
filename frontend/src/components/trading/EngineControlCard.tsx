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
  Tooltip,
  Typography,
} from '@mui/material';
import PowerSettingsNewIcon from '@mui/icons-material/PowerSettingsNew';
import SwapHorizIcon from '@mui/icons-material/SwapHoriz';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { tradingApi } from '@/lib/api/endpoints';
import { useAuth } from '@/lib/auth/AuthProvider';
import { apiErrorMessage } from '@/lib/api/client';
import type { TradingMode } from '@/lib/types/domain';

interface Props {
  onSuccess: (msg: string) => void;
  onError: (msg: string) => void;
}

export function EngineControlCard({ onSuccess, onError }: Props) {
  const { can } = useAuth();
  const qc = useQueryClient();
  const [liveConfirmOpen, setLiveConfirmOpen] = useState(false);

  const { data: status, isLoading } = useQuery({
    queryKey: ['trading', 'status'],
    queryFn: tradingApi.status,
    refetchInterval: 10_000,
  });

  const toggleAutoMutation = useMutation({
    mutationFn: (enabled: boolean) => tradingApi.setAutoTrading(enabled),
    onSuccess: () => {
      qc.invalidateQueries({ queryKey: ['trading', 'status'] });
      onSuccess('Trading automatique mis à jour');
    },
    onError: (err) => onError(apiErrorMessage(err, 'Erreur lors de la mise à jour')),
  });

  const setModeMutation = useMutation({
    mutationFn: (mode: TradingMode) => tradingApi.setMode(mode),
    onSuccess: (data) => {
      qc.invalidateQueries({ queryKey: ['trading', 'status'] });
      onSuccess(`Mode basculé vers ${data.mode.toUpperCase()}`);
      setLiveConfirmOpen(false);
    },
    onError: (err) => {
      onError(apiErrorMessage(err, 'Erreur lors du changement de mode'));
      setLiveConfirmOpen(false);
    },
  });

  const canToggleAuto = can('trading.toggle_auto');
  const canSwitchMode = can('trading.switch_mode');

  function handleAutoToggle(checked: boolean) {
    if (!canToggleAuto) return;
    toggleAutoMutation.mutate(checked);
  }

  function handleModeSwitch() {
    if (!canSwitchMode || !status) return;
    const targetMode: TradingMode = status.mode === 'paper' ? 'live' : 'paper';
    if (targetMode === 'live') {
      setLiveConfirmOpen(true);
    } else {
      setModeMutation.mutate('paper');
    }
  }

  function handleLiveConfirm() {
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

              {/* Paper / Live mode */}
              <Box>
                <Typography variant="caption" color="text.secondary" sx={{ textTransform: 'uppercase', letterSpacing: 0.6 }}>
                  Mode de trading
                </Typography>
                <Stack direction="row" alignItems="center" spacing={2} sx={{ mt: 1 }}>
                  <Box
                    sx={{
                      px: 2,
                      py: 0.5,
                      borderRadius: 1,
                      bgcolor: status?.mode === 'paper' ? 'primary.main' : 'warning.main',
                      color: '#fff',
                      fontWeight: 700,
                      fontSize: 13,
                      letterSpacing: 1,
                    }}
                  >
                    {status?.mode?.toUpperCase() ?? '—'}
                  </Box>
                  <Tooltip
                    title={!canSwitchMode ? 'Permission requise' : status?.mode === 'paper' ? 'Basculer en LIVE (ordres réels Kraken)' : 'Basculer en PAPER'}
                    disableHoverListener={false}
                  >
                    <span>
                      <Button
                        variant="outlined"
                        size="small"
                        startIcon={setModeMutation.isPending ? <CircularProgress size={14} /> : <SwapHorizIcon />}
                        onClick={handleModeSwitch}
                        disabled={!canSwitchMode || setModeMutation.isPending}
                        color={status?.mode === 'paper' ? 'warning' : 'primary'}
                      >
                        {status?.mode === 'paper' ? 'Passer en LIVE' : 'Passer en PAPER'}
                      </Button>
                    </span>
                  </Tooltip>
                </Stack>
              </Box>
            </Stack>
          )}
        </CardContent>
      </Card>

      {/* LIVE confirmation dialog */}
      <Dialog open={liveConfirmOpen} onClose={() => setLiveConfirmOpen(false)} maxWidth="sm" fullWidth>
        <DialogTitle sx={{ color: 'warning.main' }}>⚠ Activation du mode LIVE</DialogTitle>
        <DialogContent>
          <DialogContentText>
            Vous êtes sur le point de basculer en mode <strong>LIVE</strong>. Cette action activera
            les ordres réels sur <strong>Kraken</strong>. Des fonds réels seront engagés.
            Confirmez-vous ce changement de mode ?
          </DialogContentText>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setLiveConfirmOpen(false)} color="inherit">
            Annuler
          </Button>
          <Button
            onClick={handleLiveConfirm}
            color="warning"
            variant="contained"
            disabled={setModeMutation.isPending}
          >
            {setModeMutation.isPending ? <CircularProgress size={18} /> : 'Confirmer — LIVE'}
          </Button>
        </DialogActions>
      </Dialog>
    </>
  );
}
