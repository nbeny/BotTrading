'use client';

import {
  Box,
  Button,
  Card,
  CardContent,
  Chip,
  CircularProgress,
  Dialog,
  DialogActions,
  DialogContent,
  DialogContentText,
  DialogTitle,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableContainer,
  TableHead,
  TableRow,
  TextField,
  Tooltip,
  Typography,
} from '@mui/material';
import TableRowsIcon from '@mui/icons-material/TableRows';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { portfolioApi, tradingApi, type AdjustSlTpInput } from '@/lib/api/endpoints';
import { useAuth } from '@/lib/auth/AuthProvider';
import { apiErrorMessage } from '@/lib/api/client';
import { DeltaText, DirectionChip, EmptyState } from '@/components/common';
import { fmtUsd, fmtNum } from '@/lib/format';
import type { Position } from '@/lib/types/domain';

interface Props {
  onSuccess: (msg: string) => void;
  onError: (msg: string) => void;
}

interface CloseDialogState {
  open: boolean;
  position: Position | null;
}

interface SlTpDialogState {
  open: boolean;
  position: Position | null;
  stop_loss: string;
  take_profit: string;
}

export function PositionsTable({ onSuccess, onError }: Props) {
  const { can } = useAuth();
  const qc = useQueryClient();

  const [closeDialog, setCloseDialog] = useState<CloseDialogState>({ open: false, position: null });
  const [slTpDialog, setSlTpDialog] = useState<SlTpDialogState>({
    open: false,
    position: null,
    stop_loss: '',
    take_profit: '',
  });

  const canClose = can('trading.close_position');
  const canAdjust = can('trading.adjust_sltp');

  const { data: positions, isLoading } = useQuery({
    queryKey: ['portfolio', 'positions'],
    queryFn: portfolioApi.positions,
    refetchInterval: 10_000,
  });

  const closeMutation = useMutation({
    mutationFn: (id: string) => tradingApi.closePosition(id),
    onSuccess: (trade) => {
      qc.invalidateQueries({ queryKey: ['portfolio', 'positions'] });
      qc.invalidateQueries({ queryKey: ['portfolio', 'trades'] });
      onSuccess(`Position ${trade.symbol} fermée`);
      setCloseDialog({ open: false, position: null });
    },
    onError: (err) => {
      onError(apiErrorMessage(err, 'Erreur lors de la fermeture'));
      setCloseDialog({ open: false, position: null });
    },
  });

  const adjustSlTpMutation = useMutation({
    mutationFn: ({ id, input }: { id: string; input: AdjustSlTpInput }) =>
      tradingApi.adjustSlTp(id, input),
    onSuccess: (pos) => {
      qc.invalidateQueries({ queryKey: ['portfolio', 'positions'] });
      onSuccess(`SL/TP de ${pos.symbol} mis à jour`);
      setSlTpDialog({ open: false, position: null, stop_loss: '', take_profit: '' });
    },
    onError: (err) => {
      onError(apiErrorMessage(err, 'Erreur lors de la mise à jour SL/TP'));
      setSlTpDialog((d) => ({ ...d, open: false }));
    },
  });

  function openCloseDialog(pos: Position) {
    setCloseDialog({ open: true, position: pos });
  }

  function openSlTpDialog(pos: Position) {
    setSlTpDialog({
      open: true,
      position: pos,
      stop_loss: pos.stop_loss != null ? String(pos.stop_loss) : '',
      take_profit: pos.take_profit != null ? String(pos.take_profit) : '',
    });
  }

  function handleCloseConfirm() {
    if (!closeDialog.position) return;
    closeMutation.mutate(closeDialog.position.position_id);
  }

  function handleSlTpConfirm() {
    if (!slTpDialog.position) return;
    const input: AdjustSlTpInput = {
      stop_loss: slTpDialog.stop_loss ? parseFloat(slTpDialog.stop_loss) : null,
      take_profit: slTpDialog.take_profit ? parseFloat(slTpDialog.take_profit) : null,
    };
    adjustSlTpMutation.mutate({ id: slTpDialog.position.position_id, input });
  }

  return (
    <>
      <Card>
        <CardContent sx={{ pb: '16px !important' }}>
          <Stack direction="row" alignItems="center" spacing={1} sx={{ mb: 2 }}>
            <TableRowsIcon color="primary" />
            <Typography variant="h6">Positions ouvertes</Typography>
            {!isLoading && positions && (
              <Chip label={positions.length} size="small" color="primary" variant="outlined" />
            )}
          </Stack>

          {isLoading ? (
            <Box sx={{ display: 'flex', justifyContent: 'center', py: 4 }}>
              <CircularProgress />
            </Box>
          ) : !positions?.length ? (
            <EmptyState message="Aucune position ouverte" />
          ) : (
            <TableContainer>
              <Table size="small">
                <TableHead>
                  <TableRow>
                    <TableCell>Symbole</TableCell>
                    <TableCell>Direction</TableCell>
                    <TableCell align="right">Quantité</TableCell>
                    <TableCell align="right">Entrée</TableCell>
                    <TableCell align="right">Actuel</TableCell>
                    <TableCell align="right">Valeur</TableCell>
                    <TableCell align="right">PnL $</TableCell>
                    <TableCell align="right">PnL %</TableCell>
                    <TableCell align="right">SL</TableCell>
                    <TableCell align="right">TP</TableCell>
                    <TableCell align="center">Protégé</TableCell>
                    <TableCell align="center">Actions</TableCell>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {positions.map((pos) => (
                    <PositionRow
                      key={pos.position_id}
                      position={pos}
                      canClose={canClose}
                      canAdjust={canAdjust}
                      onClose={() => openCloseDialog(pos)}
                      onAdjust={() => openSlTpDialog(pos)}
                    />
                  ))}
                </TableBody>
              </Table>
            </TableContainer>
          )}
        </CardContent>
      </Card>

      {/* Close position confirmation dialog */}
      <Dialog open={closeDialog.open} onClose={() => setCloseDialog({ open: false, position: null })} maxWidth="xs" fullWidth>
        <DialogTitle>Fermer la position</DialogTitle>
        <DialogContent>
          <DialogContentText>
            Confirmer la fermeture de la position{' '}
            <strong>{closeDialog.position?.symbol}</strong> ({closeDialog.position?.direction.toUpperCase()}
            , {fmtNum(closeDialog.position?.quantity ?? 0)} unités) ?
          </DialogContentText>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setCloseDialog({ open: false, position: null })} color="inherit">
            Annuler
          </Button>
          <Button
            onClick={handleCloseConfirm}
            color="error"
            variant="contained"
            disabled={closeMutation.isPending}
          >
            {closeMutation.isPending ? <CircularProgress size={18} /> : 'Fermer la position'}
          </Button>
        </DialogActions>
      </Dialog>

      {/* Adjust SL/TP dialog */}
      <Dialog
        open={slTpDialog.open}
        onClose={() => setSlTpDialog((d) => ({ ...d, open: false }))}
        maxWidth="xs"
        fullWidth
      >
        <DialogTitle>
          Ajuster SL/TP — {slTpDialog.position?.symbol}
        </DialogTitle>
        <DialogContent>
          <Stack spacing={2} sx={{ mt: 1 }}>
            <TextField
              fullWidth
              size="small"
              label="Stop Loss (USD)"
              type="number"
              inputProps={{ min: 0, step: 'any' }}
              value={slTpDialog.stop_loss}
              onChange={(e) => setSlTpDialog((d) => ({ ...d, stop_loss: e.target.value }))}
              helperText="Laisser vide pour supprimer"
            />
            <TextField
              fullWidth
              size="small"
              label="Take Profit (USD)"
              type="number"
              inputProps={{ min: 0, step: 'any' }}
              value={slTpDialog.take_profit}
              onChange={(e) => setSlTpDialog((d) => ({ ...d, take_profit: e.target.value }))}
              helperText="Laisser vide pour supprimer"
            />
          </Stack>
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setSlTpDialog((d) => ({ ...d, open: false }))} color="inherit">
            Annuler
          </Button>
          <Button
            onClick={handleSlTpConfirm}
            color="primary"
            variant="contained"
            disabled={adjustSlTpMutation.isPending}
          >
            {adjustSlTpMutation.isPending ? <CircularProgress size={18} /> : 'Enregistrer'}
          </Button>
        </DialogActions>
      </Dialog>
    </>
  );
}

interface PositionRowProps {
  position: Position;
  canClose: boolean;
  canAdjust: boolean;
  onClose: () => void;
  onAdjust: () => void;
}

function PositionRow({ position: pos, canClose, canAdjust, onClose, onAdjust }: PositionRowProps) {
  return (
    <TableRow hover>
      <TableCell>
        <Typography variant="body2" className="mono" sx={{ fontWeight: 700 }}>
          {pos.symbol}
        </Typography>
        <Typography variant="caption" color="text.secondary">
          {pos.mode.toUpperCase()}
        </Typography>
      </TableCell>
      <TableCell>
        <DirectionChip direction={pos.direction} />
      </TableCell>
      <TableCell align="right">
        <Typography variant="body2" className="mono">
          {fmtNum(pos.quantity, 6)}
        </Typography>
      </TableCell>
      <TableCell align="right">
        <Typography variant="body2" className="mono">
          {fmtUsd(pos.entry_price)}
        </Typography>
      </TableCell>
      <TableCell align="right">
        <Typography variant="body2" className="mono">
          {fmtUsd(pos.current_price)}
        </Typography>
      </TableCell>
      <TableCell align="right">
        <Typography variant="body2" className="mono">
          {fmtUsd(pos.value_usd)}
        </Typography>
      </TableCell>
      <TableCell align="right">
        <DeltaText value={pos.unrealized_pnl_usd} suffix="$" format={(v) => fmtUsd(v)} />
      </TableCell>
      <TableCell align="right">
        <DeltaText value={pos.unrealized_pnl_pct} />
      </TableCell>
      <TableCell align="right">
        <Typography variant="body2" className="mono" color="error.main">
          {pos.stop_loss != null ? fmtUsd(pos.stop_loss) : '—'}
        </Typography>
      </TableCell>
      <TableCell align="right">
        <Typography variant="body2" className="mono" color="success.main">
          {pos.take_profit != null ? fmtUsd(pos.take_profit) : '—'}
        </Typography>
      </TableCell>
      <TableCell align="center">
        {pos.protected ? (
          <Chip label="Protégé" size="small" color="info" variant="outlined" />
        ) : (
          <Typography variant="caption" color="text.secondary">—</Typography>
        )}
      </TableCell>
      <TableCell align="center">
        <Stack direction="row" spacing={0.5} justifyContent="center">
          <Tooltip title={!canAdjust ? 'Permission requise' : 'Ajuster SL/TP'} disableHoverListener={canAdjust}>
            <span>
              <Button
                size="small"
                variant="outlined"
                color="primary"
                onClick={onAdjust}
                disabled={!canAdjust}
                sx={{ minWidth: 80, fontSize: 11 }}
              >
                SL/TP
              </Button>
            </span>
          </Tooltip>
          <Tooltip title={!canClose ? 'Permission requise' : 'Fermer la position'} disableHoverListener={canClose}>
            <span>
              <Button
                size="small"
                variant="outlined"
                color="error"
                onClick={onClose}
                disabled={!canClose}
                sx={{ minWidth: 70, fontSize: 11 }}
              >
                Fermer
              </Button>
            </span>
          </Tooltip>
        </Stack>
      </TableCell>
    </TableRow>
  );
}
