import { NextResponse, type NextRequest } from 'next/server';
import { asMockBalanceState, getPortfolio } from '@/lib/mock/store';

/**
 * `?balance=fresh|stale|unavailable` overrides the Kraken-balance reading for a
 * single call (handy for `curl`); `MOCK_BALANCE_STATE` sets it for the whole
 * server, which is the only way to drive the *UI* into a given state — the
 * terminal fetches `/portfolio` with no query string of its own, and patching
 * that just to demo a mock would be production code paying for a dev affordance.
 *
 * Defaults to `fresh` so the nominal rendering stays the one you get for free.
 */
export async function GET(req: NextRequest) {
  const override = new URL(req.url).searchParams.get('balance');
  const state = asMockBalanceState(override ?? process.env.MOCK_BALANCE_STATE);
  return NextResponse.json(getPortfolio(state));
}
