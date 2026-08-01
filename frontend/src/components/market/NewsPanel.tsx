'use client';

import {
  Box,
  Card,
  CardContent,
  Chip,
  Link,
  Skeleton,
  Stack,
  Typography,
} from '@mui/material';
import OpenInNewIcon from '@mui/icons-material/OpenInNew';
import type { NewsItem } from '@/lib/types/domain';
import { SentimentChip, EmptyState } from '@/components/common';
import { fmtRelative } from '@/lib/format';

interface Props {
  news: NewsItem[];
  loading: boolean;
  now: number;
  /** `true` dans le drawer : ni Card ni titre, le conteneur les porte déjà. */
  bare?: boolean;
}

function NewsCard({ item, now }: { item: NewsItem; now: number }) {
  return (
    <Box
      sx={{
        py: 1.75,
        px: 0,
        borderBottom: '1px solid rgba(255,255,255,0.05)',
        '&:last-child': { borderBottom: 'none' },
      }}
    >
      {/* Title + source row */}
      <Stack direction="row" justifyContent="space-between" alignItems="flex-start" gap={1}>
        <Link
          href={item.url}
          target="_blank"
          rel="noopener noreferrer"
          underline="hover"
          color="text.primary"
          sx={{
            flex: 1,
            fontSize: 14,
            fontWeight: 600,
            lineHeight: 1.4,
            display: 'flex',
            alignItems: 'flex-start',
            gap: 0.5,
            '&:hover': { color: 'primary.light' },
          }}
        >
          {item.title}
          <OpenInNewIcon sx={{ fontSize: 13, mt: 0.3, flexShrink: 0, opacity: 0.6 }} />
        </Link>
      </Stack>

      {/* Meta row */}
      <Stack direction="row" spacing={1.5} alignItems="center" flexWrap="wrap" gap={0.5} sx={{ mt: 1 }}>
        <Typography variant="caption" color="text.secondary" sx={{ fontWeight: 600 }}>
          {item.source}
        </Typography>
        <Typography variant="caption" color="text.secondary" className="mono">
          {fmtRelative(item.published_at, now)}
        </Typography>
        <SentimentChip score={item.sentiment} />
        {item.symbols.map((sym) => (
          <Chip key={sym} label={sym} size="small" variant="outlined" sx={{ height: 20, fontSize: 11 }} />
        ))}
      </Stack>
    </Box>
  );
}

export function NewsPanel({ news, loading, now, bare = false }: Props) {
  const body = loading ? (
    <Stack spacing={1}>
      {Array.from({ length: 5 }).map((_, i) => (
        <Box key={i} sx={{ py: 1 }}>
          <Skeleton variant="text" height={20} width="90%" />
          <Skeleton variant="text" height={16} width="50%" sx={{ mt: 0.5 }} />
        </Box>
      ))}
    </Stack>
  ) : news.length === 0 ? (
    <EmptyState message="Aucune news disponible." />
  ) : (
    <Box>
      {news.map((item) => (
        <NewsCard key={item.id} item={item} now={now} />
      ))}
    </Box>
  );

  if (bare) return <Box>{body}</Box>;

  return (
    <Card>
      <CardContent>
        <Typography variant="h6" sx={{ mb: 2 }}>
          News importantes
        </Typography>
        {body}
      </CardContent>
    </Card>
  );
}
