import { NextResponse } from 'next/server';
import { getThresholdReport } from '@/lib/mock/threshold';

export async function GET() {
  return NextResponse.json(getThresholdReport());
}
