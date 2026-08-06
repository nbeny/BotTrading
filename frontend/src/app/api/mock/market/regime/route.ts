import { NextResponse } from 'next/server';
import { getRegime } from '@/lib/mock/regime';

export async function GET() {
  return NextResponse.json(getRegime());
}
