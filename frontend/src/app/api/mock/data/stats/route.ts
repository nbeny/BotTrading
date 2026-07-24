import { NextResponse } from 'next/server';
import { contentStats } from '@/lib/mock/content';

export async function GET() { return NextResponse.json(contentStats()); }
