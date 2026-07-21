import { NextResponse } from 'next/server';
import { getPositions } from '@/lib/mock/store';

export async function GET() {
  return NextResponse.json(getPositions());
}
