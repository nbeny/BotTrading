import { NextResponse } from 'next/server';
import { getStoredTrace } from '@/lib/mock/sim';
import { getTrace } from '@/lib/mock/trace';

// Prefer the REAL stored lineage for this correlation_id (emitted by the
// server-side simulation); fall back to a synthesized one only for unknown ids
// (e.g. a cid from before a server restart).
export async function GET(_req: Request, { params }: { params: Promise<{ cid: string }> }) {
  const { cid } = await params;
  return NextResponse.json(getStoredTrace(cid) ?? getTrace(cid));
}
