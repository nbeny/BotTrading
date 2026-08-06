import { NextResponse } from 'next/server';
import { getJournalDecisions } from '@/lib/mock/journal';
import type { JournalWindow } from '@/lib/types/journal';

export async function GET(req: Request) {
  const { searchParams } = new URL(req.url);
  const window = (searchParams.get('window') ?? '30d') as JournalWindow;
  const limit = Number(searchParams.get('limit') ?? 50);
  const offset = Number(searchParams.get('offset') ?? 0);
  return NextResponse.json(getJournalDecisions(window, limit, offset));
}
