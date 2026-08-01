'use client';

import {
  Box,
  Card,
  CardContent,
  Chip,
  Stack,
  Typography,
  type ChipProps,
} from '@mui/material';
import ArrowDropUpIcon from '@mui/icons-material/ArrowDropUp';
import ArrowDropDownIcon from '@mui/icons-material/ArrowDropDown';
import { pnlColor } from '@/theme/theme';
import { fmtPct, scoreColor } from '@/lib/format';

export { krakenBalanceView, type KrakenBalanceView } from './KrakenBalance';
export { TurnstileWidget, type TurnstileHandle } from './TurnstileWidget';

/** Page title + optional actions row. */
export function PageHeader({
  title,
  subtitle,
  actions,
}: {
  title: string;
  subtitle?: string;
  actions?: React.ReactNode;
}) {
  return (
    <Stack
      direction={{ xs: 'column', sm: 'row' }}
      justifyContent="space-between"
      alignItems={{ xs: 'flex-start', sm: 'center' }}
      spacing={2}
      sx={{ mb: 3 }}
    >
      <Box>
        <Typography variant="h4">{title}</Typography>
        {subtitle && (
          <Typography variant="body2" color="text.secondary" sx={{ mt: 0.5 }}>
            {subtitle}
          </Typography>
        )}
      </Box>
      {actions && <Box>{actions}</Box>}
    </Stack>
  );
}

/** Colored +/- percentage or value text with directional arrow. */
export function DeltaText({
  value,
  suffix = '%',
  format,
  variant = 'body2',
  showArrow = true,
}: {
  value: number;
  suffix?: string;
  format?: (v: number) => string;
  variant?: 'body2' | 'body1' | 'h6' | 'subtitle2' | 'caption';
  showArrow?: boolean;
}) {
  const color = pnlColor(value);
  const label = format ? format(value) : suffix === '%' ? fmtPct(value) : `${value}`;
  return (
    <Stack direction="row" alignItems="center" component="span" sx={{ color }}>
      {showArrow && value !== 0 && (
        value > 0 ? (
          <ArrowDropUpIcon fontSize="small" />
        ) : (
          <ArrowDropDownIcon fontSize="small" />
        )
      )}
      <Typography variant={variant} component="span" className="mono" sx={{ fontWeight: 700, color }}>
        {label}
      </Typography>
    </Stack>
  );
}

/** KPI card: label, big value, optional delta and footnote. */
export function StatCard({
  label,
  value,
  delta,
  deltaFormat,
  icon,
  footnote,
  accent,
}: {
  label: string;
  value: React.ReactNode;
  delta?: number;
  deltaFormat?: (v: number) => string;
  icon?: React.ReactNode;
  footnote?: React.ReactNode;
  accent?: string;
}) {
  return (
    <Card sx={{ height: '100%' }}>
      <CardContent>
        <Stack direction="row" justifyContent="space-between" alignItems="flex-start">
          <Typography variant="caption" color="text.secondary" sx={{ textTransform: 'uppercase', letterSpacing: 0.6 }}>
            {label}
          </Typography>
          {icon && <Box sx={{ color: accent ?? 'primary.main', opacity: 0.9 }}>{icon}</Box>}
        </Stack>
        <Typography variant="h5" className="mono" sx={{ mt: 1, fontWeight: 800 }}>
          {value}
        </Typography>
        <Stack direction="row" spacing={1} alignItems="center" sx={{ mt: 0.5, minHeight: 22 }}>
          {delta != null && <DeltaText value={delta} format={deltaFormat} />}
          {footnote && (
            <Typography variant="caption" color="text.secondary">
              {footnote}
            </Typography>
          )}
        </Stack>
      </CardContent>
    </Card>
  );
}

/**
 * Badge de score d'opportunité.
 *
 * `score` arrive sur **0–1** : les mappers du backend (`map_token`,
 * `map_decision`) et le store mock divisent tous par 100. `scoreColor` raisonne
 * en revanche sur 0–100. Sans cette conversion, tout score non nul s'affichait
 * `1` en rouge — la colonne ne transmettait aucune information.
 */
export function ScoreChip({ score, size = 'small' }: { score: number; size?: ChipProps['size'] }) {
  const pct = Math.round(score * 100);
  return <Chip label={pct} color={scoreColor(pct)} size={size} variant="filled" />;
}

/** Directional long/short chip. */
export function DirectionChip({ direction }: { direction: 'long' | 'short' | 'watch' }) {
  const map = {
    long: { color: 'success' as const, label: 'LONG' },
    short: { color: 'error' as const, label: 'SHORT' },
    watch: { color: 'default' as const, label: 'WATCH' },
  };
  const c = map[direction] ?? map.watch;
  return <Chip label={c.label} color={c.color} size="small" variant="outlined" />;
}

export function EmptyState({ message }: { message: string }) {
  return (
    <Box sx={{ py: 6, textAlign: 'center' }}>
      <Typography variant="body2" color="text.secondary">
        {message}
      </Typography>
    </Box>
  );
}

/** Sentiment score (-1..1) as a colored chip. */
export function SentimentChip({ score }: { score: number }) {
  const color = score > 0.15 ? 'success' : score < -0.15 ? 'error' : 'default';
  const label = score > 0.15 ? 'Bullish' : score < -0.15 ? 'Bearish' : 'Neutre';
  return <Chip label={`${label} ${score.toFixed(2)}`} color={color} size="small" variant="outlined" />;
}
