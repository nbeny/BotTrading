import { NextResponse } from 'next/server';
import { getJournalAttribution } from '@/lib/mock/journal';
import type { JournalWindow } from '@/lib/types/journal';

export async function GET(req: Request) {
  const { searchParams } = new URL(req.url);
  const window = (searchParams.get('window') ?? '30d') as JournalWindow;
  return NextResponse.json(getJournalAttribution(window));
}
