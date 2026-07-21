import { NextResponse } from 'next/server';
import { placeOrder } from '@/lib/mock/store';
import type { ManualOrderInput } from '@/lib/api/endpoints';

export async function POST(req: Request) {
  const body = await req.json().catch(() => ({})) as Partial<ManualOrderInput>;
  const { symbol, side, order_type, quantity, price } = body;

  if (!symbol || !side || !order_type || !quantity) {
    return NextResponse.json({ detail: 'Missing required fields' }, { status: 400 });
  }

  const trade = placeOrder({ symbol, side, order_type, quantity, price });
  return NextResponse.json(trade);
}
