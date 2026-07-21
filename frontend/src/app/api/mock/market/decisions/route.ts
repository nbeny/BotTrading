import { NextResponse } from 'next/server';
import { getDecisions } from '@/lib/mock/store';

export async function GET(req: Request) {
  const { searchParams } = new URL(req.url);
  const limit = Number(searchParams.get('limit') ?? 30);
  return NextResponse.json(getDecisions(limit));
}
