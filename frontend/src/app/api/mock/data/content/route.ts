import { NextResponse } from 'next/server';
import { queryContent } from '@/lib/mock/content';
import type { ContentCategory } from '@/lib/types/content';

export async function GET(req: Request) {
  const p = new URL(req.url).searchParams;
  return NextResponse.json(queryContent({
    category: (p.get('category') as ContentCategory) ?? 'all',
    symbol: p.get('symbol') ?? undefined,
    q: p.get('q') ?? undefined,
    sentiment: (p.get('sentiment') as 'all' | 'pos' | 'neg' | 'neu') ?? 'all',
    limit: p.get('limit') ? Number(p.get('limit')) : 50,
    offset: p.get('offset') ? Number(p.get('offset')) : 0,
  }));
}
