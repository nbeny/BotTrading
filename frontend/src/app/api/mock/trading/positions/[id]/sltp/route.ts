import { NextResponse } from 'next/server';
import { adjustSlTp } from '@/lib/mock/store';
import type { AdjustSlTpInput } from '@/lib/api/endpoints';

export async function PATCH(
  req: Request,
  { params }: { params: Promise<{ id: string }> },
) {
  const { id } = await params;
  const body = await req.json().catch(() => ({})) as AdjustSlTpInput;
  const position = adjustSlTp(id, body);
  if (!position) {
    return NextResponse.json({ detail: 'Position not found' }, { status: 404 });
  }
  return NextResponse.json(position);
}
