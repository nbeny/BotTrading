import { NextResponse } from 'next/server';
import { getTradingStatus } from '@/lib/mock/store';

export async function GET() {
  return NextResponse.json(getTradingStatus());
}
