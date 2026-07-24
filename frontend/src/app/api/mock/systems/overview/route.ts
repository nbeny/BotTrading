import { NextResponse } from 'next/server';
import { getSystemsSnapshot } from '@/lib/mock/systems';

export async function GET() {
  return NextResponse.json(getSystemsSnapshot());
}
