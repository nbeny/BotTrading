import { NextResponse } from 'next/server';
import { getDossier } from '@/lib/mock/dossier';

export async function GET(
  _req: Request,
  { params }: { params: Promise<{ symbol: string }> },
) {
  const { symbol } = await params;
  const dossier = getDossier(symbol);
  if (!dossier) {
    return NextResponse.json({ detail: 'Token not found' }, { status: 404 });
  }
  return NextResponse.json(dossier);
}
