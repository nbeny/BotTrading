'use client';

import { Box, Skeleton } from '@mui/material';
import AccountBalanceWalletIcon from '@mui/icons-material/AccountBalanceWallet';
import CurrencyExchangeIcon from '@mui/icons-material/CurrencyExchange';
import ShowChartIcon from '@mui/icons-material/ShowChart';
import TrendingUpIcon from '@mui/icons-material/TrendingUp';
import { StatCard, krakenBalanceView } from '@/components/common';
import { fmtUsd, fmtPct } from '@/lib/format';
import type { Portfolio, Position } from '@/lib/types/domain';

interface KpiRowProps {
  portfolio: Portfolio | undefined;
  positions: Position[] | undefined;
  isLoading: boolean;
}

export function KpiRow({ portfolio, positions, isLoading }: KpiRowProps) {
  if (isLoading) {
    return (
      <Box
        sx={{
          display: 'grid',
          gap: 2,
          gridTemplateColumns: { xs: '1fr', sm: '1fr 1fr', lg: 'repeat(4, 1fr)' },
        }}
      >
        {Array.from({ length: 4 }).map((_, i) => (
          <Skeleton key={i} variant="rectangular" height={120} sx={{ borderRadius: 2 }} />
        ))}
      </Box>
    );
  }

  const openCount = positions?.length ?? 0;
  // Portfolio refetches every 6–15 s, so a per-render clock is never more than
  // one poll behind — no extra interval needed just to age a timestamp.
  const kraken = portfolio ? krakenBalanceView(portfolio, Date.now()) : null;

  return (
    <Box
      sx={{
        display: 'grid',
        gap: 2,
        gridTemplateColumns: { xs: '1fr', sm: '1fr 1fr', lg: 'repeat(4, 1fr)' },
      }}
    >
      <StatCard
        label="Valeur totale portefeuille"
        value={portfolio ? fmtUsd(portfolio.total_value_usd) : '—'}
        delta={portfolio?.pnl_24h_pct}
        deltaFormat={fmtPct}
        icon={<AccountBalanceWalletIcon />}
        footnote="24h"
        accent="#5b8def"
      />
      {/*
        Three distinct readings, none of which may be confused with another:
        no portfolio at all (`—`, the pre-data case), no Kraken snapshot
        (`— · non connecté`), and an actual balance. `0 $` is never one of them.
      */}
      <StatCard
        label="Balance Kraken"
        value={kraken ? kraken.value : '—'}
        icon={<CurrencyExchangeIcon />}
        footnote={
          portfolio ? (
            <>
              Investi : {fmtUsd(portfolio.invested_usd)}
              {kraken?.note ? <> · {kraken.note}</> : null}
            </>
          ) : undefined
        }
        accent="#8b5cf6"
      />
      <StatCard
        label="Positions ouvertes"
        value={String(openCount)}
        icon={<ShowChartIcon />}
        footnote={openCount === 1 ? '1 position active' : `${openCount} positions actives`}
        accent="#ffb547"
      />
      <StatCard
        label="PnL temps réel"
        value={portfolio ? fmtUsd(portfolio.unrealized_pnl_usd) : '—'}
        delta={portfolio?.unrealized_pnl_pct}
        deltaFormat={fmtPct}
        icon={<TrendingUpIcon />}
        footnote="Non réalisé"
        accent={portfolio && portfolio.unrealized_pnl_usd >= 0 ? '#26d07c' : '#ff5370'}
      />
    </Box>
  );
}
