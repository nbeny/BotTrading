import { NextResponse } from 'next/server';
import { getEventPage } from '@/lib/mock/eventPage';

// Cursor-paginated archive. `before` is the previous page's `next_cursor`;
// `types` is a comma-separated event-type filter.
export async function GET(req: Request) {
  const p = new URL(req.url).searchParams;
  const limit = p.get('limit') ? Number(p.get('limit')) : 100;
  return NextResponse.json(
    getEventPage({
      limit: Number.isFinite(limit) ? limit : 100,
      before: p.get('before'),
      types: p.get('types'),
      symbol: p.get('symbol'),
    }),
  );
}
