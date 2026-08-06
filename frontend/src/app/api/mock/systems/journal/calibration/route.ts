import { NextResponse } from 'next/server';
import { getJournalCalibration } from '@/lib/mock/journal';
import type { JournalWindow } from '@/lib/types/journal';

export async function GET(req: Request) {
  const { searchParams } = new URL(req.url);
  const window = (searchParams.get('window') ?? '30d') as JournalWindow;
  const threshold = Number(searchParams.get('threshold') ?? 70);
  return NextResponse.json(getJournalCalibration(window, threshold));
}
