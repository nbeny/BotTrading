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
import { ScoreChip, EmptyState } from '@/components/common';
import { fmtRelative } from '@/lib/format';
import type { AnalysisEvent, DecisionEvent, PriceEvent, SentimentEvent } from '@/lib/types/events';
import type { Opportunity } from '@/lib/types/domain';

type Signal = PriceEvent | AnalysisEvent | SentimentEvent | DecisionEvent;

interface SignalsTableProps {
  signals: Signal[] | undefined;
  opportunities: Opportunity[] | undefined;
  isLoadingSignals: boolean;
  isLoadingOpportunities: boolean;
  now: number;
}

function getSignalScore(s: Signal): number | null {
  if (s.event_type === 'AnalysisEvent') return s.opportunity_score;
  if (s.event_type === 'DecisionEvent') return s.opportunity_score;
  return null;
}

function getSignalReason(s: Signal): string {
  if (s.event_type === 'AnalysisEvent') return s.reason;
  if (s.event_type === 'DecisionEvent') return s.rationale.slice(0, 80) + (s.rationale.length > 80 ? '…' : '');
  if (s.event_type === 'SentimentEvent') return `Sentiment ${s.sentiment_score.toFixed(2)} · ${s.input_kind}`;
  if (s.event_type === 'PriceEvent') return `${s.price_change_pct_24h > 0 ? '+' : ''}${s.price_change_pct_24h}% /24h`;
  return '';
}

function getSignalLabel(s: Signal): { label: string; color: 'info' | 'warning' | 'primary' | 'secondary' | 'default' } {
  if (s.event_type === 'AnalysisEvent') return { label: 'Analyse', color: 'warning' };
  if (s.event_type === 'DecisionEvent') return { label: 'Décision', color: 'primary' };
  if (s.event_type === 'SentimentEvent') return { label: 'Sentiment', color: 'secondary' };
  return { label: 'Prix', color: 'info' };
}

export function SignalsTable({
  signals,
  opportunities,
  isLoadingSignals,
  isLoadingOpportunities,
  now,
}: SignalsTableProps) {
  const isLoading = isLoadingSignals || isLoadingOpportunities;

  return (
    <Card>
      <CardContent>
        <Typography variant="h6" sx={{ mb: 2 }}>
          Signaux &amp; Opportunités
        </Typography>

        {isLoading ? (
          <Stack spacing={1}>
            {Array.from({ length: 4 }).map((_, i) => (
              <Skeleton key={i} variant="rectangular" height={36} sx={{ borderRadius: 1 }} />
            ))}
          </Stack>
        ) : (
          <>
            {/* Opportunities section */}
            {opportunities && opportunities.length > 0 && (
              <>
                <Typography variant="caption" color="text.secondary" sx={{ textTransform: 'uppercase', letterSpacing: 0.6, mb: 1, display: 'block' }}>
                  Scores Opportunités
                </Typography>
                <Table size="small" sx={{ mb: 3 }}>
                  <TableHead>
                    <TableRow>
                      <TableCell>Symbole</TableCell>
                      <TableCell>Direction</TableCell>
                      <TableCell>Score</TableCell>
                      <TableCell>Raison</TableCell>
                      <TableCell>Heure</TableCell>
                    </TableRow>
                  </TableHead>
                  <TableBody>
                    {opportunities.slice(0, 5).map((opp) => (
                      <TableRow key={opp.opportunity_id} hover>
                        <TableCell>
                          <Typography variant="body2" className="mono" fontWeight={600}>
                            {opp.symbol}
                          </Typography>
                        </TableCell>
                        <TableCell>
                          <Chip
                            label={opp.direction.toUpperCase()}
                            color={opp.direction === 'long' ? 'success' : 'error'}
                            size="small"
                            variant="outlined"
                          />
                        </TableCell>
                        <TableCell>
                          <ScoreChip score={opp.opportunity_score} />
                        </TableCell>
                        <TableCell>
                          <Typography variant="caption" color="text.secondary">
                            {opp.rationale.slice(0, 70)}{opp.rationale.length > 70 ? '…' : ''}
                          </Typography>
                        </TableCell>
                        <TableCell>
                          <Typography variant="caption" color="text.secondary" className="mono">
                            {fmtRelative(opp.created_at, now)}
                          </Typography>
                        </TableCell>
                      </TableRow>
                    ))}
                  </TableBody>
                </Table>
              </>
            )}

            {/* Signals section */}
            <Typography variant="caption" color="text.secondary" sx={{ textTransform: 'uppercase', letterSpacing: 0.6, mb: 1, display: 'block' }}>
              Derniers Signaux Détectés
            </Typography>
            {(!signals || signals.length === 0) && (!opportunities || opportunities.length === 0) ? (
              <EmptyState message="Aucun signal disponible pour le moment." />
            ) : signals && signals.length > 0 ? (
              <Table size="small">
                <TableHead>
                  <TableRow>
                    <TableCell>Type</TableCell>
                    <TableCell>Symbole</TableCell>
                    <TableCell>Score</TableCell>
                    <TableCell>Raison</TableCell>
                    <TableCell>Heure</TableCell>
                  </TableRow>
                </TableHead>
                <TableBody>
                  {signals.slice(0, 8).map((s, i) => {
                    const meta = getSignalLabel(s);
                    const score = getSignalScore(s);
                    const reason = getSignalReason(s);
                    return (
                      <TableRow key={`${s.event_id ?? i}-${i}`} hover>
                        <TableCell>
                          <Chip label={meta.label} color={meta.color} size="small" />
                        </TableCell>
                        <TableCell>
                          <Typography variant="body2" className="mono" fontWeight={600}>
                            {s.symbol}
                          </Typography>
                        </TableCell>
                        <TableCell>
                          {score !== null ? <ScoreChip score={score} /> : <Typography variant="caption" color="text.secondary">—</Typography>}
                        </TableCell>
                        <TableCell>
                          <Typography variant="caption" color="text.secondary">
                            {reason}
                          </Typography>
                        </TableCell>
                        <TableCell>
                          <Typography variant="caption" color="text.secondary" className="mono">
                            {fmtRelative(s.occurred_at, now)}
                          </Typography>
                        </TableCell>
                      </TableRow>
                    );
                  })}
                </TableBody>
              </Table>
            ) : null}
          </>
        )}
      </CardContent>
    </Card>
  );
}
