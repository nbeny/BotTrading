import { NextResponse } from 'next/server';
import { approveOpportunity } from '@/lib/mock/store';

export async function POST(
  _req: Request,
  { params }: { params: Promise<{ id: string }> },
) {
  const { id } = await params;
  const opp = approveOpportunity(id);
  if (!opp) {
    return NextResponse.json({ detail: 'Opportunity not found' }, { status: 404 });
  }
  return NextResponse.json(opp);
}
