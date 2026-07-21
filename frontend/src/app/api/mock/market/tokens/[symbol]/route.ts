import { NextResponse } from 'next/server';
import { getToken } from '@/lib/mock/store';

export async function GET(
  _req: Request,
  { params }: { params: Promise<{ symbol: string }> },
) {
  const { symbol } = await params;
  const token = getToken(symbol);
  if (!token) {
    return NextResponse.json({ detail: 'Token not found' }, { status: 404 });
  }
  return NextResponse.json(token);
}
