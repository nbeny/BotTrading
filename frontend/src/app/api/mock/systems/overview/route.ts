import { NextResponse } from 'next/server';
import { getSystemsSnapshot } from '@/lib/mock/systems';

export async function GET(req: Request) {
  const { searchParams } = new URL(req.url);
  const window = searchParams.get('window') ?? '24h';
  // Echo the requested window back so the T14 selector visibly does something
  // in mock mode — the underlying pipeline numbers don't actually vary by
  // window here, only the label the graph renders above them.
  return NextResponse.json({ ...getSystemsSnapshot(), pipeline_window: window });
}
