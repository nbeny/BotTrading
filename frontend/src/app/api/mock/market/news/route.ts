import { NextResponse } from 'next/server';
import { getNews } from '@/lib/mock/store';

export async function GET(req: Request) {
  const { searchParams } = new URL(req.url);
  const limit = Number(searchParams.get('limit') ?? 20);
  return NextResponse.json(getNews(limit));
}
