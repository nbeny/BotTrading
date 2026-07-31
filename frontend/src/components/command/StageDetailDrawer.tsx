'use client';
import { useQuery } from '@tanstack/react-query';
import { Box, Button, Chip, CircularProgress, Drawer, IconButton, Stack, Typography } from '@mui/material';
import CloseIcon from '@mui/icons-material/Close';
import { systemsApi } from '@/lib/api/endpoints';
import { apiErrorMessage } from '@/lib/api/client';
import { fmtNum, fmtRelative } from '@/lib/format';
import type { StageItem, SystemsWindow } from '@/lib/types/systems';

function ItemRow({ item, now, onTrace }: { item: StageItem; now: number; onTrace: (cid: string) => void }) {
  const clickable = !!item.correlation_id;
  return (
    <Box
      role={clickable ? 'button' : undefined}
      tabIndex={clickable ? 0 : undefined}
      onClick={clickable ? () => onTrace(item.correlation_id!) : undefined}
      onKeyDown={
        clickable
          ? (e) => {
              if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault();
                onTrace(item.correlation_id!);
              }
            }
          : undefined
      }
      sx={{
        p: 1.25,
        borderRadius: 2,
        border: '1px solid',
        borderColor: 'divider',
        // A row without a correlation_id must not look clickable — no
        // pointer cursor, no hover affordance, nothing to click through to.
        cursor: clickable ? 'pointer' : 'default',
        transition: 'border-color .16s ease, background-color .16s ease',
        '&:hover': clickable
          ? { borderColor: 'primary.main', bgcolor: 'rgba(77,159,255,0.06)' }
          : undefined,
      }}
    >
      <Stack direction="row" justifyContent="space-between" alignItems="flex-start" spacing={1}>
        <Typography variant="body2" sx={{ flex: 1 }}>
          {item.summary}
        </Typography>
        <Typography variant="caption" color="text.secondary" className="mono" sx={{ whiteSpace: 'nowrap' }}>
          {fmtRelative(item.at, now)}
        </Typography>
      </Stack>
      <Stack direction="row" flexWrap="wrap" useFlexGap sx={{ gap: 0.5, mt: 0.75 }}>
        {Object.entries(item.detail).map(([k, v]) => (
          <Chip
            key={k}
            size="small"
            variant="outlined"
            className="mono"
            label={`${k}: ${v}`}
            sx={{ height: 18, fontSize: 9.5 }}
          />
        ))}
      </Stack>
    </Box>
  );
}

export function StageDetailDrawer({
  stageId,
  window,
  onClose,
  onTrace,
}: {
  stageId: string | null;
  window: SystemsWindow;
  onClose: () => void;
  onTrace: (cid: string) => void;
}) {
  const { data, isLoading, isError, error, refetch } = useQuery({
    queryKey: ['systems', 'stage', stageId, window],
    queryFn: () => systemsApi.stage(stageId!, window),
    enabled: !!stageId,
    refetchInterval: 30000,
  });
  const now = Date.now();

  return (
    <Drawer
      anchor="right"
      open={!!stageId}
      onClose={onClose}
      PaperProps={{ sx: { width: { xs: '100%', sm: 460 }, bgcolor: 'rgba(8,11,20,0.92)', backdropFilter: 'blur(16px)', p: 2.5 } }}
    >
      <Stack direction="row" justifyContent="space-between" alignItems="center" sx={{ mb: 2 }}>
        <Box>
          <Typography variant="overline" color="text.secondary">
            Étape · {window}
          </Typography>
          <Typography variant="h6">{data?.label ?? '…'}</Typography>
        </Box>
        <IconButton onClick={onClose}>
          <CloseIcon />
        </IconButton>
      </Stack>

      {isLoading && (
        <Stack alignItems="center" sx={{ py: 6 }}>
          <CircularProgress size={28} />
        </Stack>
      )}

      {!isLoading && isError && (
        <Stack spacing={1.5} sx={{ py: 4 }} alignItems="flex-start">
          <Typography variant="body2" color="error.main">
            {apiErrorMessage(error, "Impossible de charger le détail de l'étape")}
          </Typography>
          <Button size="small" variant="outlined" onClick={() => refetch()}>
            Réessayer
          </Button>
        </Stack>
      )}

      {!isLoading && !isError && data && (
        <Stack spacing={2}>
          <Stack direction="row" spacing={3}>
            <Box>
              <Typography variant="caption" color="text.secondary">
                Volume
              </Typography>
              <Typography className="mono" sx={{ fontWeight: 700, fontSize: 22 }}>
                {fmtNum(data.volume, 0)}
              </Typography>
            </Box>
            <Box>
              <Typography variant="caption" color="text.secondary">
                Rejetés
              </Typography>
              <Typography className="mono" sx={{ fontWeight: 700, fontSize: 22 }}>
                {fmtNum(data.dropped, 0)}
              </Typography>
            </Box>
          </Stack>

          {data.breakdown.length > 0 && (
            <Stack direction="row" flexWrap="wrap" useFlexGap sx={{ gap: 0.75 }}>
              {data.breakdown.map((b) => (
                <Chip
                  key={b.key}
                  size="small"
                  variant="outlined"
                  label={`${b.key} (${b.count})`}
                  sx={{ fontSize: 10.5, height: 22 }}
                />
              ))}
            </Stack>
          )}

          {data.items.length === 0 ? (
            <Typography variant="body2" color="text.secondary" sx={{ py: 3, textAlign: 'center' }}>
              Aucun élément sur cette fenêtre.
            </Typography>
          ) : (
            <Stack spacing={1}>
              {data.items.map((item, i) => (
                <ItemRow key={`${item.at}-${i}`} item={item} now={now} onTrace={onTrace} />
              ))}
            </Stack>
          )}
        </Stack>
      )}
    </Drawer>
  );
}
