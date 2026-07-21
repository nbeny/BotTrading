'use client';

import {
  Card,
  CardContent,
  Chip,
  Skeleton,
  Stack,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  Typography,
} from '@mui/material';
import { EmptyState } from '@/components/common';
import { fmtUsd, fmtNum, fmtDateTime } from '@/lib/format';
import { pnlColor } from '@/theme/theme';
import type { Trade } from '@/lib/types/domain';

interface TradesTableProps {
  trades: Trade[] | undefined;
  isLoading: boolean;
}

export function TradesTable({ trades, isLoading }: TradesTableProps) {
  return (
    <Card>
      <CardContent>
        <Typography variant="h6" sx={{ mb: 2 }}>
          Historique des Trades
        </Typography>

        {isLoading ? (
          <Stack spacing={1}>
            {Array.from({ length: 5 }).map((_, i) => (
              <Skeleton key={i} variant="rectangular" height={36} sx={{ borderRadius: 1 }} />
            ))}
          </Stack>
        ) : !trades || trades.length === 0 ? (
          <EmptyState message="Aucun trade récent." />
        ) : (
          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell>Symbole</TableCell>
                <TableCell>Sens</TableCell>
                <TableCell align="right">Prix</TableCell>
                <TableCell align="right">Qté</TableCell>
                <TableCell align="right">PnL</TableCell>
                <TableCell>Heure</TableCell>
              </TableRow>
            </TableHead>
            <TableBody>
              {trades.map((trade) => {
                const pnl = trade.pnl_usd;
                const pnlStr = pnl !== null ? fmtUsd(pnl) : '—';
                const color = pnl !== null ? pnlColor(pnl) : '#94a0b8';
                return (
                  <TableRow key={trade.trade_id} hover>
                    <TableCell>
                      <Typography variant="body2" className="mono" fontWeight={600}>
                        {trade.symbol}
                      </Typography>
                    </TableCell>
                    <TableCell>
                      <Chip
                        label={trade.side.toUpperCase()}
                        color={trade.side === 'buy' ? 'success' : 'error'}
                        size="small"
                        variant="outlined"
                      />
                    </TableCell>
                    <TableCell align="right">
                      <Typography variant="body2" className="mono">
                        {fmtUsd(trade.price)}
                      </Typography>
                    </TableCell>
                    <TableCell align="right">
                      <Typography variant="body2" className="mono">
                        {fmtNum(trade.quantity, 4)}
                      </Typography>
                    </TableCell>
                    <TableCell align="right">
                      <Typography variant="body2" className="mono" sx={{ color, fontWeight: 600 }}>
                        {pnlStr}
                      </Typography>
                    </TableCell>
                    <TableCell>
                      <Typography variant="caption" color="text.secondary" className="mono">
                        {fmtDateTime(trade.executed_at)}
                      </Typography>
                    </TableCell>
                  </TableRow>
                );
              })}
            </TableBody>
          </Table>
        )}
      </CardContent>
    </Card>
  );
}
