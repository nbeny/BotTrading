'use client';

import { Box, Snackbar, Alert, Stack } from '@mui/material';
import { useState, useCallback } from 'react';
import { PageHeader } from '@/components/common';
import { EngineControlCard } from '@/components/trading/EngineControlCard';
import { ManualOrderCard } from '@/components/trading/ManualOrderCard';
import { OpportunitiesSection } from '@/components/trading/OpportunitiesSection';
import { PositionsTable } from '@/components/trading/PositionsTable';

interface SnackState {
  open: boolean;
  message: string;
  severity: 'success' | 'error';
}

export default function TradingPage() {
  const [snack, setSnack] = useState<SnackState>({ open: false, message: '', severity: 'success' });

  const showSuccess = useCallback((msg: string) => {
    setSnack({ open: true, message: msg, severity: 'success' });
  }, []);

  const showError = useCallback((msg: string) => {
    setSnack({ open: true, message: msg, severity: 'error' });
  }, []);

  function handleSnackClose() {
    setSnack((s) => ({ ...s, open: false }));
  }

  return (
    <Box sx={{ p: { xs: 2, sm: 3 } }}>
      <PageHeader
        title="Panneau de contrôle"
        subtitle="Gestion du moteur de trading, ordres manuels, opportunités IA et positions"
      />

      {/* Top row: Engine control + Manual order */}
      <Box
        sx={{
          display: 'grid',
          gap: 2,
          gridTemplateColumns: { xs: '1fr', md: '1fr 1fr' },
          mb: 3,
        }}
      >
        <EngineControlCard onSuccess={showSuccess} onError={showError} />
        <ManualOrderCard onSuccess={showSuccess} onError={showError} />
      </Box>

      {/* Opportunities section */}
      <Box sx={{ mb: 3 }}>
        <OpportunitiesSection onSuccess={showSuccess} onError={showError} />
      </Box>

      {/* Positions table — full width */}
      <Stack>
        <PositionsTable onSuccess={showSuccess} onError={showError} />
      </Stack>

      {/* Feedback snackbar */}
      <Snackbar
        open={snack.open}
        autoHideDuration={4000}
        onClose={handleSnackClose}
        anchorOrigin={{ vertical: 'bottom', horizontal: 'right' }}
      >
        <Alert onClose={handleSnackClose} severity={snack.severity} variant="filled" sx={{ width: '100%' }}>
          {snack.message}
        </Alert>
      </Snackbar>
    </Box>
  );
}
