import { NextResponse } from 'next/server';
import { getTokens } from '@/lib/mock/store';

export async function GET() {
  return NextResponse.json(getTokens());
}
