import { NextResponse } from 'next/server';
import { getTrace } from '@/lib/mock/trace';

export async function GET(_req: Request, { params }: { params: Promise<{ cid: string }> }) {
  const { cid } = await params;
  return NextResponse.json(getTrace(cid));
}
