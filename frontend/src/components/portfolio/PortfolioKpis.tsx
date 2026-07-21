'use client';

import { Box, Skeleton } from '@mui/material';
import AccountBalanceWalletIcon from '@mui/icons-material/AccountBalanceWallet';
import CurrencyExchangeIcon from '@mui/icons-material/CurrencyExchange';
import SavingsIcon from '@mui/icons-material/Savings';
import TrendingUpIcon from '@mui/icons-material/TrendingUp';
import ShowChartIcon from '@mui/icons-material/ShowChart';
import BarChartIcon from '@mui/icons-material/BarChart';
import { StatCard } from '@/components/common';
import { fmtUsd, fmtPct } from '@/lib/format';
import type { Portfolio } from '@/lib/types/domain';

interface Props {
  portfolio: Portfolio | undefined;
  isLoading: boolean;
}

export function PortfolioKpis({ portfolio, isLoading }: Props) {
  if (isLoading) {
    return (
      <Box
        sx={{
          display: 'grid',
          gap: 2,
          gridTemplateColumns: { xs: '1fr', sm: '1fr 1fr', md: 'repeat(3, 1fr)', lg: 'repeat(6, 1fr)' },
        }}
      >
        {Array.from({ length: 6 }).map((_, i) => (
          <Skeleton key={i} variant="rectangular" height={110} sx={{ borderRadius: 2 }} />
        ))}
      </Box>
    );
  }

  if (!portfolio) return null;

  return (
    <Box
      sx={{
        display: 'grid',
        gap: 2,
        gridTemplateColumns: { xs: '1fr', sm: '1fr 1fr', md: 'repeat(3, 1fr)', lg: 'repeat(6, 1fr)' },
      }}
    >
      <StatCard
        label="Valeur totale"
        value={fmtUsd(portfolio.total_value_usd)}
        icon={<AccountBalanceWalletIcon fontSize="small" />}
        accent="#5b8def"
        footnote={`Mis à jour ${new Date(portfolio.updated_at).toLocaleTimeString('fr-FR', { hour: '2-digit', minute: '2-digit' })}`}
      />
      <StatCard
        label="Balance Kraken"
        value={fmtUsd(portfolio.kraken_balance_usd)}
        icon={<CurrencyExchangeIcon fontSize="small" />}
        accent="#8b5cf6"
      />
      <StatCard
        label="Cash disponible"
        value={fmtUsd(portfolio.cash_usd)}
        icon={<SavingsIcon fontSize="small" />}
        accent="#26d07c"
      />
      <StatCard
        label="Investi"
        value={fmtUsd(portfolio.invested_usd)}
        icon={<TrendingUpIcon fontSize="small" />}
        accent="#ffb547"
      />
      <StatCard
        label="PnL latent"
        value={fmtUsd(portfolio.unrealized_pnl_usd)}
        delta={portfolio.unrealized_pnl_pct}
        deltaFormat={fmtPct}
        icon={<ShowChartIcon fontSize="small" />}
        accent={portfolio.unrealized_pnl_usd >= 0 ? '#26d07c' : '#ff5370'}
      />
      <StatCard
        label="PnL réalisé 24h"
        value={fmtUsd(portfolio.realized_pnl_24h_usd)}
        delta={portfolio.pnl_24h_pct}
        deltaFormat={fmtPct}
        icon={<BarChartIcon fontSize="small" />}
        accent={portfolio.realized_pnl_24h_usd >= 0 ? '#26d07c' : '#ff5370'}
      />
    </Box>
  );
}
