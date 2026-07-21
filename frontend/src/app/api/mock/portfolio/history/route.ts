import { NextResponse } from 'next/server';
import { getPortfolioHistory } from '@/lib/mock/store';

export async function GET(req: Request) {
  const { searchParams } = new URL(req.url);
  const range = searchParams.get('range') ?? '30d';
  return NextResponse.json(getPortfolioHistory(range));
}
