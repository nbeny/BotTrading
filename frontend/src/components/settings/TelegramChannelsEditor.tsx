'use client';

import { useEffect, useState } from 'react';
import { Box, Button, Chip, Stack, TextField, Tooltip, Typography } from '@mui/material';
import CancelIcon from '@mui/icons-material/Cancel';
import ReportProblemIcon from '@mui/icons-material/ReportProblem';
import type { SourceStatus } from '@/lib/api/endpoints';
import { fmtRelative } from '@/lib/format';

/** Past this age a reading is shown as frozen instead of live.
 *
 *  The provider polls every 300s, so this is roughly three missed cycles: long
 *  enough that a slow cycle or a flood-wait backoff is not called stale, short
 *  enough that a key frozen because the operator switched Telegram off stops
 *  passing for a live reading. The key is durable and the poll loop skips the
 *  fetch entirely while the platform is off, so a success published in March
 *  still reads `ok: true` today — `updated_at` is the only thing that tells
 *  those two apart. */
const STALE_AFTER_MS = 15 * 60 * 1000;

/** How often the rendered ages are recomputed. Only affects the "il y a …"
 *  text and the fresh/stale verdict; the data itself is refetched by the
 *  parent panel. */
const AGE_TICK_MS = 30_000;

function SourceHealth({
  status,
  now,
  enabled,
}: {
  status: SourceStatus | undefined;
  now: number;
  enabled: boolean;
}) {
  // Switched off outranks every reading, and is checked before them: the poll
  // loop skips the provider entirely, so the durable key stops moving at the
  // exact moment it stops meaning anything. Whatever it still holds — a green,
  // a fault, an hour-old stamp — describes the last cycle before the operator
  // pulled the switch, and rendering any of it would report on a source that
  // is not running.
  if (!enabled) {
    return (
      <Chip
        size="small"
        variant="outlined"
        data-testid="telegram-health"
        label="Source coupée — aucune collecte en cours"
      />
    );
  }

  // Absent from `source_status` = never reported. Not healthy, not broken —
  // unknown, and said so rather than painted green.
  if (!status) {
    return (
      <Chip
        size="small"
        variant="outlined"
        data-testid="telegram-health"
        label="État inconnu — aucune remontée de la source"
      />
    );
  }

  const age = now - new Date(status.updated_at).getTime();
  const stale = !Number.isFinite(age) || age > STALE_AFTER_MS;
  // `ok` alone drives the fault indicator: a reason is explanatory text, not a
  // symptom, and is routinely present while the source is perfectly healthy.
  const health = !status.ok
    ? ({ color: 'error', label: 'Source en défaut' } as const)
    : stale
      ? ({ color: 'default', label: 'Lecture figée' } as const)
      : ({ color: 'success', label: 'Source active' } as const);

  return (
    <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap" useFlexGap>
      <Chip
        size="small"
        color={health.color}
        variant={health.color === 'success' ? 'outlined' : 'filled'}
        label={health.label}
        data-testid="telegram-health"
      />
      <Typography variant="caption" color="text.secondary" data-testid="telegram-health-age">
        {fmtRelative(status.updated_at, now)}
      </Typography>
      {status.reason && (
        <Typography variant="caption" color="text.secondary" data-testid="telegram-health-reason">
          — {status.reason}
        </Typography>
      )}
    </Stack>
  );
}

export interface TelegramChannelsEditorProps {
  channels: string[];
  /** `undefined` when Telegram has never published health. */
  status: SourceStatus | undefined;
  /** Whether the platform switch is on, i.e. whether the loop still polls.
   *  The list stays editable either way — configuring the desk before turning
   *  it on is the natural order — but the health readout does not, since a key
   *  nothing rewrites cannot describe a running source. */
  enabled: boolean;
  busy: boolean;
  /** Posts the **complete** list; resolves true when the server accepted it. */
  onSave: (channels: string[]) => Promise<boolean>;
}

export function TelegramChannelsEditor({
  channels,
  status,
  enabled,
  busy,
  onSave,
}: TelegramChannelsEditorProps) {
  const [draft, setDraft] = useState('');
  const [now, setNow] = useState(() => Date.now());

  useEffect(() => {
    const id = setInterval(() => setNow(Date.now()), AGE_TICK_MS);
    return () => clearInterval(id);
  }, []);

  const writtenOff = status?.channels ?? {};

  const add = async (e: React.FormEvent) => {
    e.preventDefault();
    const entry = draft.trim();
    if (!entry) return;
    // The server replaces the list wholesale, so every edit posts the whole
    // array. It also owns normalization (`@x`, `t.me/x` -> `x`), dedup and
    // rejection — sending the raw entry means what comes back is exactly what
    // gets polled, and a bad handle earns a 422 the panel shows.
    if (await onSave([...channels, entry])) setDraft('');
  };

  // Clearing the list is a legitimate action meaning "poll nobody", so the last
  // removal posts `[]` like any other edit rather than being suppressed.
  const remove = (handle: string) => onSave(channels.filter((c) => c !== handle));

  return (
    <Box sx={{ pl: 4, pt: 1 }}>
      <Stack direction="row" spacing={1} alignItems="center" flexWrap="wrap" useFlexGap sx={{ mb: 1 }}>
        <Typography variant="body2" sx={{ fontWeight: 700 }}>
          Canaux Telegram
        </Typography>
        <SourceHealth status={status} now={now} enabled={enabled} />
      </Stack>

      {channels.length === 0 ? (
        <Typography variant="body2" color="text.secondary" sx={{ mb: 1 }}>
          Aucun canal configuré — la source n’interroge personne.
        </Typography>
      ) : (
        <Stack direction="row" flexWrap="wrap" useFlexGap spacing={1} sx={{ mb: 1 }}>
          {channels.map((handle) => {
            const why = writtenOff[handle];
            const chip = (
              <Chip
                key={handle}
                size="small"
                label={handle}
                color={why ? 'warning' : 'default'}
                variant={why ? 'filled' : 'outlined'}
                icon={why ? <ReportProblemIcon /> : undefined}
                // The accessible name carries the fault so the reason is not
                // hover-only: a mistyped handle is otherwise invisible.
                aria-label={why ? `${handle} — non lisible : ${why}` : handle}
                disabled={busy}
                onDelete={() => remove(handle)}
                deleteIcon={<CancelIcon titleAccess={`Retirer ${handle}`} />}
              />
            );
            return why ? (
              <Tooltip key={handle} title={`Non lisible : ${why}`}>
                <span>{chip}</span>
              </Tooltip>
            ) : (
              chip
            );
          })}
        </Stack>
      )}

      <Stack direction="row" spacing={1} alignItems="center" component="form" onSubmit={add}>
        <TextField
          size="small"
          label="Ajouter un canal"
          placeholder="@nom ou t.me/nom"
          value={draft}
          disabled={busy}
          onChange={(e) => setDraft(e.target.value)}
          sx={{ minWidth: 260 }}
        />
        <Button type="submit" variant="outlined" size="small" disabled={busy || !draft.trim()}>
          Ajouter
        </Button>
      </Stack>
    </Box>
  );
}
