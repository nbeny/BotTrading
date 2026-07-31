import { NextResponse } from 'next/server';
import { getSystemsSnapshot } from '@/lib/mock/systems';
import { scaleCount } from '@/lib/mock/window';

export async function GET(req: Request) {
  const { searchParams } = new URL(req.url);
  const window = searchParams.get('window') ?? '24h';
  const snap = getSystemsSnapshot();
  // Volumes move with the window; throughput does not, because it is an
  // instantaneous rate rather than a count over the period. Without this the
  // selector changed only a label, and nobody checking it in mock mode — the
  // repo default, and the only place this feature is seen by eye — could tell a
  // working control from a dead one.
  const pipeline = snap.pipeline.map((s) => ({
    ...s,
    volume: s.volume === null ? null : scaleCount(s.volume, window),
    dropped: s.dropped === null ? null : scaleCount(s.dropped, window),
  }));
  return NextResponse.json({ ...snap, pipeline, pipeline_window: window });
}
