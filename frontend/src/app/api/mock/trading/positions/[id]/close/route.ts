import { NextResponse } from 'next/server';
import { closePosition } from '@/lib/mock/store';

export async function POST(
  _req: Request,
  { params }: { params: Promise<{ id: string }> },
) {
  const { id } = await params;
  const trade = closePosition(id);
  if (!trade) {
    return NextResponse.json({ detail: 'Position not found' }, { status: 404 });
  }
  return NextResponse.json(trade);
}
