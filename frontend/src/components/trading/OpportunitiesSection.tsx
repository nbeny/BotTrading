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
  DialogTitle,
  Stack,
  TextField,
  Tooltip,
  Typography,
} from '@mui/material';
import CheckCircleOutlineIcon from '@mui/icons-material/CheckCircleOutline';
import CancelOutlinedIcon from '@mui/icons-material/CancelOutlined';
import LightbulbOutlinedIcon from '@mui/icons-material/LightbulbOutlined';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useState } from 'react';
import { tradingApi } from '@/lib/api/endpoints';
import { useAuth } from '@/lib/auth/AuthProvider';
import { apiErrorMessage } from '@/lib/api/client';
import { DirectionChip, ScoreChip, EmptyState } from '@/components/common';
import { fmtUsd, fmtPct, fmtNum } from '@/lib/format';
import type { Opportunity } from '@/lib/types/domain';

interface Props {
  onSuccess: (msg: string) => void;
  onError: (msg: string) => void;
}

interface RejectDialogState {
  open: boolean;
  opportunityId: string;
  reason: string;
}

export function OpportunitiesSection({ onSuccess, onError }: Props) {
  const { can } = useAuth();
  const qc = useQueryClient();
  const [rejectDialog, setRejectDialog] = useState<RejectDialogState>({
    open: false,
    opportunityId: '',
    reason: '',
  });

  const canApprove = can('trading.approve_opportunity');

  const { data: opportunities, isLoading } = useQuery({
    queryKey: ['trading', 'opportunities', 'pending'],
    queryFn: () => tradingApi.opportunities('pending'),
    refetchInterval: 15_000,
  });

  const approveMutation = useMutation({
    mutationFn: (id: string) => tradingApi.approveOpportunity(id),
    onSuccess: (opp) => {
      qc.invalidateQueries({ queryKey: ['trading', 'opportunities'] });
      onSuccess(`Opportunité ${opp.symbol} validée`);
    },
    onError: (err) => onError(apiErrorMessage(err, "Erreur lors de la validation")),
  });

  const rejectMutation = useMutation({
    mutationFn: ({ id, reason }: { id: string; reason?: string }) =>
      tradingApi.rejectOpportunity(id, reason),
    onSuccess: (opp) => {
      qc.invalidateQueries({ queryKey: ['trading', 'opportunities'] });
      onSuccess(`Opportunité ${opp.symbol} refusée`);
      setRejectDialog({ open: false, opportunityId: '', reason: '' });
    },
    onError: (err) => {
      onError(apiErrorMessage(err, "Erreur lors du refus"));
      setRejectDialog((d) => ({ ...d, open: false }));
    },
  });

  function openRejectDialog(id: string) {
    setRejectDialog({ open: true, opportunityId: id, reason: '' });
  }

  function handleRejectConfirm() {
    rejectMutation.mutate({
      id: rejectDialog.opportunityId,
      reason: rejectDialog.reason.trim() || undefined,
    });
  }

  return (
    <>
      <Box>
        <Stack direction="row" alignItems="center" spacing={1} sx={{ mb: 2 }}>
          <LightbulbOutlinedIcon color="primary" />
          <Typography variant="h6">Opportunités IA à valider</Typography>
          {!isLoading && opportunities && (
            <Chip label={opportunities.length} size="small" color="primary" variant="outlined" />
          )}
        </Stack>

        {isLoading ? (
          <Box sx={{ display: 'flex', justifyContent: 'center', py: 4 }}>
            <CircularProgress />
          </Box>
        ) : !opportunities?.length ? (
          <EmptyState message="Aucune opportunité en attente de validation" />
        ) : (
          <Box
            sx={{
              display: 'grid',
              gap: 2,
              gridTemplateColumns: { xs: '1fr', md: '1fr 1fr', xl: 'repeat(3, 1fr)' },
            }}
          >
            {opportunities.map((opp) => (
              <OpportunityCard
                key={opp.opportunity_id}
                opportunity={opp}
                canApprove={canApprove}
                approving={approveMutation.isPending && approveMutation.variables === opp.opportunity_id}
                rejecting={rejectMutation.isPending && rejectMutation.variables?.id === opp.opportunity_id}
                onApprove={() => approveMutation.mutate(opp.opportunity_id)}
                onReject={() => openRejectDialog(opp.opportunity_id)}
              />
            ))}
          </Box>
        )}
      </Box>

      {/* Reject dialog */}
      <Dialog open={rejectDialog.open} onClose={() => setRejectDialog((d) => ({ ...d, open: false }))} maxWidth="sm" fullWidth>
        <DialogTitle>Refuser l'opportunité</DialogTitle>
        <DialogContent>
          <TextField
            fullWidth
            multiline
            rows={3}
            label="Raison (optionnel)"
            value={rejectDialog.reason}
            onChange={(e) => setRejectDialog((d) => ({ ...d, reason: e.target.value }))}
            sx={{ mt: 1 }}
          />
        </DialogContent>
        <DialogActions>
          <Button onClick={() => setRejectDialog((d) => ({ ...d, open: false }))} color="inherit">
            Annuler
          </Button>
          <Button
            onClick={handleRejectConfirm}
            color="error"
            variant="contained"
            disabled={rejectMutation.isPending}
          >
            {rejectMutation.isPending ? <CircularProgress size={18} /> : 'Confirmer le refus'}
          </Button>
        </DialogActions>
      </Dialog>
    </>
  );
}

interface OpportunityCardProps {
  opportunity: Opportunity;
  canApprove: boolean;
  approving: boolean;
  rejecting: boolean;
  onApprove: () => void;
  onReject: () => void;
}

function OpportunityCard({ opportunity: opp, canApprove, approving, rejecting, onApprove, onReject }: OpportunityCardProps) {
  return (
    <Card>
      <CardContent>
        {/* Header row */}
        <Stack direction="row" justifyContent="space-between" alignItems="flex-start" sx={{ mb: 1.5 }}>
          <Stack direction="row" spacing={1} alignItems="center">
            <Typography variant="subtitle2" className="mono" sx={{ fontWeight: 700, fontSize: 15 }}>
              {opp.symbol}
            </Typography>
            <DirectionChip direction={opp.direction} />
          </Stack>
          <Stack direction="row" spacing={0.5} alignItems="center">
            <ScoreChip score={opp.opportunity_score} />
          </Stack>
        </Stack>

        {/* Confidence */}
        <Typography variant="caption" color="text.secondary">
          Confiance:{' '}
          <Box component="span" className="mono" sx={{ fontWeight: 700, color: 'text.primary' }}>
            {fmtPct(opp.confidence * 100, 1)}
          </Box>
          {' · '}
          R/R:{' '}
          <Box component="span" className="mono" sx={{ fontWeight: 700, color: 'text.primary' }}>
            {fmtNum(opp.risk_reward_ratio, 2)}
          </Box>
        </Typography>

        {/* Price levels */}
        <Box
          sx={{
            display: 'grid',
            gridTemplateColumns: 'repeat(3, 1fr)',
            gap: 1,
            mt: 1.5,
            mb: 1.5,
          }}
        >
          {[
            { label: 'Entrée', value: opp.entry_price },
            { label: 'SL', value: opp.stop_loss },
            { label: 'TP', value: opp.take_profit },
          ].map(({ label, value }) => (
            <Box key={label}>
              <Typography variant="caption" color="text.secondary" sx={{ display: 'block' }}>
                {label}
              </Typography>
              <Typography variant="caption" className="mono" sx={{ fontWeight: 700 }}>
                {fmtUsd(value)}
              </Typography>
            </Box>
          ))}
        </Box>

        {/* Rationale */}
        <Typography
          variant="caption"
          color="text.secondary"
          sx={{
            display: '-webkit-box',
            WebkitLineClamp: 2,
            WebkitBoxOrient: 'vertical',
            overflow: 'hidden',
            mb: 1,
          }}
        >
          {opp.rationale}
        </Typography>

        {/* Key risks */}
        {opp.key_risks.length > 0 && (
          <Stack direction="row" spacing={0.5} flexWrap="wrap" sx={{ mb: 1.5, gap: 0.5 }}>
            {opp.key_risks.map((risk) => (
              <Chip key={risk} label={risk} size="small" color="warning" variant="outlined" sx={{ fontSize: 10 }} />
            ))}
          </Stack>
        )}

        {/* Actions */}
        <Stack direction="row" spacing={1} sx={{ mt: 'auto' }}>
          <Tooltip title={!canApprove ? 'Permission requise' : ''} disableHoverListener={canApprove}>
            <span style={{ flex: 1 }}>
              <Button
                fullWidth
                variant="contained"
                color="success"
                size="small"
                startIcon={approving ? <CircularProgress size={14} /> : <CheckCircleOutlineIcon />}
                onClick={onApprove}
                disabled={!canApprove || approving || rejecting}
              >
                Valider
              </Button>
            </span>
          </Tooltip>
          <Tooltip title={!canApprove ? 'Permission requise' : ''} disableHoverListener={canApprove}>
            <span style={{ flex: 1 }}>
              <Button
                fullWidth
                variant="outlined"
                color="error"
                size="small"
                startIcon={rejecting ? <CircularProgress size={14} /> : <CancelOutlinedIcon />}
                onClick={onReject}
                disabled={!canApprove || approving || rejecting}
              >
                Refuser
              </Button>
            </span>
          </Tooltip>
        </Stack>
      </CardContent>
    </Card>
  );
}
