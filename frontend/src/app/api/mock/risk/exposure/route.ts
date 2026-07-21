import { NextResponse } from 'next/server';
import { getRiskExposure } from '@/lib/mock/store';

export async function GET() {
  return NextResponse.json(getRiskExposure());
}
