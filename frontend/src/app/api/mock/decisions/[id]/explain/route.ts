import { NextResponse } from 'next/server';
import { getExplain } from '@/lib/mock/journal';

export async function GET(_req: Request, { params }: { params: Promise<{ id: string }> }) {
  const { id } = await params;
  if (id === 'unknown') return NextResponse.json({ detail: 'unknown decision' }, { status: 404 });
  return NextResponse.json(getExplain(id));
}
