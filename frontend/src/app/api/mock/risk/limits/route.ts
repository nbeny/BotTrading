import { NextResponse } from 'next/server';
import { getRiskLimits } from '@/lib/mock/store';

export async function GET() {
  return NextResponse.json(getRiskLimits());
}
