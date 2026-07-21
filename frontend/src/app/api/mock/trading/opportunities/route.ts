import { NextResponse } from 'next/server';
import { getOpportunities } from '@/lib/mock/store';

export async function GET(req: Request) {
  const { searchParams } = new URL(req.url);
  const status = (searchParams.get('status') ?? 'pending') as 'pending' | 'all';
  return NextResponse.json(getOpportunities(status));
}
