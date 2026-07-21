import { NextResponse } from 'next/server';
import { rejectOpportunity } from '@/lib/mock/store';

export async function POST(
  req: Request,
  { params }: { params: Promise<{ id: string }> },
) {
  const { id } = await params;
  const body = await req.json().catch(() => ({})) as { reason?: string };
  const opp = rejectOpportunity(id, body.reason);
  if (!opp) {
    return NextResponse.json({ detail: 'Opportunity not found' }, { status: 404 });
  }
  return NextResponse.json(opp);
}
