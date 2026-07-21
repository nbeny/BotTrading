import { NextResponse } from 'next/server';
import { getPriceHistory } from '@/lib/mock/store';

export async function GET(
  req: Request,
  { params }: { params: Promise<{ symbol: string }> },
) {
  const { symbol } = await params;
  const { searchParams } = new URL(req.url);
  const range = searchParams.get('range') ?? '1d';
  return NextResponse.json(getPriceHistory(symbol, range));
}
