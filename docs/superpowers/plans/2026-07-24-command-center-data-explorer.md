# Command Center + Data Explorer Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a real-time **Command Center** landing page (with a click-to-open Decision Trace drawer) and a **Data Explorer** page (all collected content + charts/stats), merge Portefeuille+Risque into **Capital & Risque**, demote Systèmes, and remove Dashboard — reusing the existing futuristic design system.

**Architecture:** Frontend-only (Next.js App Router, `frontend/`). Real-time data comes from the existing WebSocket mock (`MockEventSource`) + React Query REST reads against the built-in mock BFF (`/api/mock/*`). New pages compose new components under `components/command/` and `components/data/`, reusing primitives from `components/systems/common.tsx` (`HealthDot`, `Sparkline`, `MiniBar`, `SectionCard`) and `PipelineFlow`. Live-mode backend endpoints are documented gaps, not built.

**Tech Stack:** Next.js 15, React 19, MUI 6 + Emotion, TanStack Query 5, Recharts 2, Zustand, TypeScript. Verification: `npm run typecheck`, `npm run lint`, Playwright (visual + `scrollWidth===clientWidth`). No unit-test runner exists in the frontend (intentionally not added).

**Reference spec:** `docs/superpowers/specs/2026-07-24-command-center-data-explorer-design.md`

---

## Verification convention (used by every task)

Since there is no frontend unit-test runner, each task's "verify" steps are:
- `cd frontend && npm run typecheck` → expect no errors.
- `cd frontend && npm run lint` → expect "No ESLint warnings or errors".
- Where behavior is added, a Playwright check (dev server on :3000, mock mode) as described in the task.

Dev server + auth for Playwright checks (mock mode, `.env.local` already sets `NEXT_PUBLIC_USE_MOCK=1`):
```js
// login helper run via browser_evaluate before navigating to a guarded page
async () => {
  const r = await fetch('/api/mock/auth/login', {method:'POST',headers:{'Content-Type':'application/json'},
    body: JSON.stringify({email:'admin@cmi.io',password:'demo1234'})});
  localStorage.setItem('cmi.access_token', (await r.json()).access_token);
}
```

---

## File Structure

**New pages**
- `frontend/src/app/(app)/command/page.tsx` — Command Center (landing)
- `frontend/src/app/(app)/data/page.tsx` — Data Explorer
- `frontend/src/app/(app)/capital/page.tsx` — merged Portefeuille+Risque

**New components**
- `frontend/src/components/command/{KpiTicker,LiveEventStream,AiDecisionFeed,LivePnlPanel,MarketHeatPanel,GuardrailPanel,HealthRail,DecisionTraceDrawer}.tsx`
- `frontend/src/components/data/{DataStatsRow,IngestionVolumeChart,SentimentTrendChart,TopSourcesChart,MentionsChart,DataFilterBar,ContentFirehoseTable,ContentDetailDrawer}.tsx`

**New types** — `frontend/src/lib/types/content.ts` (RawContentItem, DataStats, DecisionTrace)

**New mock + BFF**
- `frontend/src/lib/mock/content.ts`, `frontend/src/lib/mock/trace.ts`
- `frontend/src/app/api/mock/data/content/route.ts`, `.../data/stats/route.ts`, `.../trace/[cid]/route.ts`

**Modified**
- `frontend/src/lib/ws/mockStream.ts` — correlated event chains
- `frontend/src/lib/api/endpoints.ts` — `dataApi`, `traceApi`
- `frontend/src/components/layout/navItems.ts` — new order, drop Dashboard, demote Systèmes
- `frontend/next.config.mjs` or route redirects — `/`,`/dashboard`→`/command`; `/portfolio`,`/risk`→`/capital`
- Delete `frontend/src/app/(app)/dashboard/page.tsx`

---

## Task 1: Navigation restructure

**Files:**
- Modify: `frontend/src/components/layout/navItems.ts`

- [ ] **Step 1: Rewrite nav items** — new order, drop Dashboard, demote Systèmes (remove from main list).

```ts
import HubIcon from '@mui/icons-material/Hub';
import InsightsIcon from '@mui/icons-material/Insights';
import DatabaseIcon from '@mui/icons-material/Storage';
import CandlestickChartIcon from '@mui/icons-material/CandlestickChart';
import AccountBalanceWalletIcon from '@mui/icons-material/AccountBalanceWallet';
import SettingsIcon from '@mui/icons-material/Settings';
import SpaceDashboardIcon from '@mui/icons-material/SpaceDashboard';
import type { SvgIconComponent } from '@mui/icons-material';

export interface NavItem { href: string; label: string; icon: SvgIconComponent; description: string; }

export const NAV_ITEMS: NavItem[] = [
  { href: '/command', label: 'Command Center', icon: SpaceDashboardIcon, description: 'Temps réel & décisions' },
  { href: '/data', label: 'Data Explorer', icon: DatabaseIcon, description: 'Contenu collecté & stats' },
  { href: '/market', label: 'Market Intelligence', icon: InsightsIcon, description: 'Tokens & IA' },
  { href: '/trading', label: 'Trading', icon: CandlestickChartIcon, description: 'Contrôle & ordres' },
  { href: '/capital', label: 'Capital & Risque', icon: AccountBalanceWalletIcon, description: 'PnL, positions & risque' },
  { href: '/settings', label: 'Paramètres', icon: SettingsIcon, description: 'Session & RBAC' },
];

// Systèmes stays routable but out of the primary nav (linked from Command Center health rail).
export const SECONDARY_NAV: NavItem[] = [
  { href: '/systems', label: 'Systèmes', icon: HubIcon, description: 'Observabilité infra' },
];
```

- [ ] **Step 2: Verify** — `npm run typecheck` (expect pass — note `AppShell` only imports `NAV_ITEMS`, still valid). `npm run lint` clean.
- [ ] **Step 3: Commit**
```bash
git add frontend/src/components/layout/navItems.ts
git commit -m "feat(nav): restructure nav — add Command Center/Data, merge Capital, demote Systèmes"
```

---

## Task 2: Redirects for removed/merged routes

**Files:**
- Modify: `frontend/next.config.mjs`

- [ ] **Step 1: Add redirects()** — map old routes to new. Open `next.config.mjs`; inside the exported config object add:

```js
async redirects() {
  return [
    { source: '/', destination: '/command', permanent: false },
    { source: '/dashboard', destination: '/command', permanent: false },
    { source: '/portfolio', destination: '/capital', permanent: false },
    { source: '/risk', destination: '/capital', permanent: false },
  ];
},
```
Note: the existing `app/page.tsx` may already redirect `/`. If it does and conflicts, keep the `next.config` redirect and leave `app/page.tsx` (config redirects run first). If `app/(app)` has its own index, unaffected.

- [ ] **Step 2: Verify** — `npm run typecheck`/`lint` clean. (Behavioral redirect check happens in Task 16.)
- [ ] **Step 3: Commit**
```bash
git add frontend/next.config.mjs
git commit -m "feat(routing): redirect / and /dashboard→/command, /portfolio /risk→/capital"
```

---

## Task 3: Capital & Risque merged page

**Files:**
- Create: `frontend/src/app/(app)/capital/page.tsx`
- Reference (reuse, do not modify): `components/portfolio/*`, `components/risk/*`

- [ ] **Step 1: Read the two existing pages** to copy their exact query wiring:
Run: open `frontend/src/app/(app)/portfolio/page.tsx` and `frontend/src/app/(app)/risk/page.tsx`. Reuse their `useQuery` calls and component props verbatim.

- [ ] **Step 2: Create the merged page** with MUI tabs.

```tsx
'use client';

import { useState } from 'react';
import { Box, Tab, Tabs } from '@mui/material';
import { PageHeader } from '@/components/common';
import PortfolioPage from '@/app/(app)/portfolio/page';
import RiskPage from '@/app/(app)/risk/page';

export default function CapitalPage() {
  const [tab, setTab] = useState(0);
  return (
    <Box sx={{ p: { xs: 2, md: 3 } }}>
      <PageHeader title="Capital & Risque" subtitle="Positions, PnL et historique · exposition, limites et alertes" />
      <Tabs value={tab} onChange={(_, v) => setTab(v)} sx={{ mb: 2 }}>
        <Tab label="Capital" />
        <Tab label="Risque" />
      </Tabs>
      <Box hidden={tab !== 0}>{tab === 0 && <PortfolioPage />}</Box>
      <Box hidden={tab !== 1}>{tab === 1 && <RiskPage />}</Box>
    </Box>
  );
}
```
Note: reusing the page components keeps DRY. If either page component renders its own `PageHeader`/outer padding that looks doubled, inline their bodies instead (copy the JSX minus the outer `<Box p>`/`PageHeader`). Verify visually in Task 16 and adjust.

- [ ] **Step 3: Verify** — `npm run typecheck`/`lint` clean.
- [ ] **Step 4: Commit**
```bash
git add frontend/src/app/\(app\)/capital/page.tsx
git commit -m "feat(capital): merged Portefeuille+Risque page with tabs"
```

---

## Task 4: Content & trace domain types

**Files:**
- Create: `frontend/src/lib/types/content.ts`

- [ ] **Step 1: Define types.**

```ts
/** A single collected item (mirrors backend raw_content + content_sentiment_agg). */
export interface RawContentItem {
  id: string;
  platform: string;                 // 'Reddit', 'Bluesky', 'CoinDesk', 'GDELT', 'CoinGecko'...
  source_category: 'social' | 'news' | 'market';
  symbols: string[];
  title: string;
  snippet: string;
  url: string | null;
  published_at: string;
  collected_at: string;
  sentiment_score: number;          // -1..1
  sentiment_confidence: number;     // 0..1
  model_name: string;               // e.g. 'ElKulako/cryptobert'
  sample_size: number;
  /** Present when this item fed a decision (links into the Decision Trace). */
  derived_decision: {
    direction: 'long' | 'short' | null;
    opportunity_score: number | null;
    correlation_id: string | null;
  } | null;
}

export interface DataStats {
  total_24h: number;
  social_24h: number;
  news_24h: number;
  market_24h: number;
  avg_sentiment: number;
  /** per-hour ingestion, last 12h */
  volume_series: { hour: string; social: number; news: number; market: number }[];
  /** sentiment trend, last 12h */
  sentiment_series: { hour: string; sentiment: number }[];
  top_sources: { source: string; count: number }[];
  mentions: { symbol: string; count: number }[];
  updated_at: string;
}

export type ContentCategory = 'all' | 'social' | 'news' | 'market';

export interface ContentQuery {
  category?: ContentCategory;
  symbol?: string;
  q?: string;
  sentiment?: 'all' | 'pos' | 'neg' | 'neu';
  limit?: number;
  offset?: number;
}

export interface ContentPage {
  items: RawContentItem[];
  total: number;
  offset: number;
  limit: number;
}

/** One stage of an end-to-end decision lineage, keyed by correlation_id. */
export interface DecisionTrace {
  correlation_id: string;
  symbol: string;
  stages: {
    kind: 'price' | 'sentiment' | 'analysis' | 'decision' | 'risk' | 'order';
    at: string | null;
    reached: boolean;
    summary: string;
    detail: Record<string, string | number | boolean | null>;
  }[];
}
```

- [ ] **Step 2: Verify** — `npm run typecheck` clean.
- [ ] **Step 3: Commit**
```bash
git add frontend/src/lib/types/content.ts
git commit -m "feat(types): RawContentItem, DataStats, DecisionTrace"
```

---

## Task 5: Correlated event chains in the mock WS stream

**Files:**
- Modify: `frontend/src/lib/ws/mockStream.ts`

**Why:** the Decision Trace needs a subset of events that share a `correlation_id` across price→analysis→decision→risk→order so a clicked event shows a complete lineage.

- [ ] **Step 1: Add a correlated-chain emitter.** In `MockEventSource`, add a field and a method, and schedule it. Keep existing timers. Add:

```ts
// field
private chainCorr: string | null = null;

// in start(), add:
this.timers.push(setInterval(() => this.correlatedChain(), 13_000));

// new method — emits a full lineage sharing one correlation_id, staggered
private correlatedChain() {
  const t = pick(UNIVERSE);
  const corr = uid('corr');
  const withCorr = <T extends CmiEvent>(e: T): T => ({ ...e, correlation_id: corr });
  const price = t.price;
  // ① price
  this.emit(withCorr({ ...this.base('PriceEvent', 'coingecko', t.symbol), coin_id: t.coin_id,
    price_usd: String(round(price, 4)), volume_24h_usd: String(Math.round(rand(2e7, 4e9))),
    price_change_pct_24h: round(rand(2, 10), 2), is_trending: true } as PriceEvent), 'market.price.events');
  // ② analysis (Haiku)
  setTimeout(() => this.emit(withCorr({ ...this.base('AnalysisEvent', 'ai-worker-haiku', t.symbol),
    opportunity_score: Math.floor(rand(78, 94)), confidence: round(rand(0.7, 0.95), 2), reason: reason(),
    price_change_pct_24h: round(rand(2, 12), 1), volume_spike_ratio: round(rand(1.8, 4), 1),
    sentiment_score: round(rand(0.2, 0.8), 2), social_growth: round(rand(0.3, 1.6), 2), escalate: true } as AnalysisEvent),
    'market.analysis.events'), 900);
  // ③ decision (Sonnet)
  setTimeout(() => this.emit(withCorr({ ...this.base('DecisionEvent', 'ai-worker-sonnet', t.symbol),
    direction: 'long', opportunity_score: Math.floor(rand(80, 92)), confidence: round(rand(0.72, 0.93), 2),
    rationale: 'Momentum soutenu, liquidité saine et sentiment corroborant. Entrée justifiée par le R/R.',
    key_risks: ['volatilité macro', 'corrélation BTC élevée'], ai_validated: true } as DecisionEvent),
    'decision.events'), 1800);
  // ④ risk
  setTimeout(() => this.emit(withCorr({ ...this.base('RiskApprovedEvent', 'risk-engine', t.symbol),
    direction: 'long', entry_price: round(price, 2), stop_loss: round(price * 0.95, 2),
    take_profit: round(price * 1.11, 2), confidence: round(rand(0.72, 0.9), 2),
    position_size_pct: round(rand(0.02, 0.06), 3), risk_reward_ratio: round(rand(1.8, 2.8), 2) } as RiskApprovedEvent),
    'risk.approved.events'), 2700);
  // ⑤ order
  setTimeout(() => this.emit(withCorr({ ...this.base('OrderExecutedEvent', 'trading-engine', t.symbol),
    order_id: uid('ord'), side: 'buy', order_type: 'market', price: round(price, 2), volume: round(rand(0.1, 3), 3),
    cost: round(rand(500, 6000), 2), fee: round(rand(1, 12), 2), status: 'filled', mode: 'dry_run' } as OrderExecutedEvent),
    'trading.orders.events'), 3600);
}
```
Add any missing imports at top: ensure `PriceEvent, AnalysisEvent, DecisionEvent, RiskApprovedEvent, OrderExecutedEvent` are imported (they already are).

- [ ] **Step 2: Verify** — `npm run typecheck` clean.
- [ ] **Step 3: Commit**
```bash
git add frontend/src/lib/ws/mockStream.ts
git commit -m "feat(mock-ws): emit correlated end-to-end event chains for decision tracing"
```

---

## Task 6: Trace mock generator + BFF route + endpoint

**Files:**
- Create: `frontend/src/lib/mock/trace.ts`
- Create: `frontend/src/app/api/mock/trace/[cid]/route.ts`
- Modify: `frontend/src/lib/api/endpoints.ts`

- [ ] **Step 1: Trace generator** — synthesizes a coherent lineage for any correlation id (fallback when the live chain isn't buffered).

```ts
import { pick, rand, round, UNIVERSE } from './universe';
import type { DecisionTrace } from '@/lib/types/content';

function iso(offsetMs: number) { return new Date(Date.now() + offsetMs).toISOString(); }

export function getTrace(cid: string): DecisionTrace {
  const t = pick(UNIVERSE);
  const price = round(t.price, 2);
  return {
    correlation_id: cid,
    symbol: t.symbol,
    stages: [
      { kind: 'price', at: iso(-42000), reached: true, summary: `Tick prix ${t.symbol} $${price}`,
        detail: { source: 'coingecko', price_usd: price, change_24h_pct: round(rand(2, 9), 2) } },
      { kind: 'sentiment', at: iso(-38000), reached: true, summary: 'Sentiment social/news agrégé',
        detail: { score: round(rand(0.2, 0.7), 2), model: 'ElKulako/cryptobert', sample: Math.floor(rand(30, 200)) } },
      { kind: 'analysis', at: iso(-30000), reached: true, summary: 'Haiku — triage (escalade)',
        detail: { opportunity_score: Math.floor(rand(78, 92)), confidence: round(rand(0.7, 0.95), 2), escalate: true } },
      { kind: 'decision', at: iso(-22000), reached: true, summary: 'Sonnet — LONG validé',
        detail: { direction: 'long', confidence: round(rand(0.72, 0.93), 2), ai_validated: true } },
      { kind: 'risk', at: iso(-12000), reached: true, summary: 'Risque — sizing & protection',
        detail: { size_pct: round(rand(2, 6), 1), stop_loss: round(price * 0.95, 2), take_profit: round(price * 1.11, 2), rr: round(rand(1.8, 2.8), 2) } },
      { kind: 'order', at: iso(-6000), reached: true, summary: 'Ordre exécuté (paper)',
        detail: { status: 'filled', price, fee: round(rand(1, 12), 2), mode: 'dry_run' } },
    ],
  };
}
```

- [ ] **Step 2: BFF route.**

```ts
import { NextResponse } from 'next/server';
import { getTrace } from '@/lib/mock/trace';

export async function GET(_req: Request, { params }: { params: Promise<{ cid: string }> }) {
  const { cid } = await params;
  return NextResponse.json(getTrace(cid));
}
```
(Next.js 15 route params are async — note the `Promise` type + `await`.)

- [ ] **Step 3: Endpoint.** In `endpoints.ts` add the import and API object:

```ts
import type { ContentPage, DataStats, DecisionTrace, ContentQuery } from '@/lib/types/content';

export const traceApi = {
  get: (cid: string) => api.get<DecisionTrace>(`/trace/${cid}`).then((r) => r.data),
};
```

- [ ] **Step 4: Verify** — `npm run typecheck`/`lint` clean. Manual: dev server up, `fetch('/api/mock/trace/corr_x').then(r=>r.json())` returns 6 stages.
- [ ] **Step 5: Commit**
```bash
git add frontend/src/lib/mock/trace.ts frontend/src/app/api/mock/trace frontend/src/lib/api/endpoints.ts
git commit -m "feat(trace): mock decision-lineage generator + BFF route + traceApi"
```

---

## Task 7: Content mock generator + BFF routes + endpoint

**Files:**
- Create: `frontend/src/lib/mock/content.ts`
- Create: `frontend/src/app/api/mock/data/content/route.ts`
- Create: `frontend/src/app/api/mock/data/stats/route.ts`
- Modify: `frontend/src/lib/api/endpoints.ts`

- [ ] **Step 1: Content generator.** Build a stable pool of ~400 items + filtering + stats.

```ts
import { pick, rand, round, uid, UNIVERSE } from './universe';
import type { ContentPage, ContentQuery, DataStats, RawContentItem } from '@/lib/types/content';

const SOCIAL = ['Reddit', 'Bluesky', 'Mastodon', '4chan /biz/', 'Farcaster', 'YouTube', 'Lens'];
const NEWS = ['CoinDesk', 'CoinTelegraph', 'The Block', 'Decrypt', 'GDELT', 'RSS', 'NewsData'];
const MARKET = ['CoinGecko', 'DexScreener'];
const SNIPPETS = [
  'ETF inflows accelerating, whales accumulating on-chain',
  'liquidations en cascade si le support cède',
  'momentum fort, volume 2× la moyenne 30j',
  'partenariat institutionnel annoncé ce matin',
  'MiCA entre en vigueur en Europe',
  'breakout de résistance majeure confirmé',
  'sentiment social en forte hausse sur 24h',
  'profondeur de carnet faible, prudence',
];

function iso(offsetMs: number) { return new Date(Date.now() + offsetMs).toISOString(); }
function catOf(i: number): RawContentItem['source_category'] { return i % 5 < 3 ? 'social' : i % 5 === 3 ? 'news' : 'market'; }
function platformFor(cat: RawContentItem['source_category']) {
  return cat === 'social' ? pick(SOCIAL) : cat === 'news' ? pick(NEWS) : pick(MARKET);
}

let _pool: RawContentItem[] = Array.from({ length: 400 }, (_, i) => {
  const cat = catOf(i);
  const tok = pick(UNIVERSE);
  const s = round(rand(-0.8, 0.9), 2);
  const hasDecision = s > 0.55 || s < -0.45;
  return {
    id: uid('rc'),
    platform: platformFor(cat),
    source_category: cat,
    symbols: [tok.symbol, ...(Math.random() > 0.7 ? [pick(UNIVERSE).symbol] : [])],
    title: `${tok.symbol} — ${pick(SNIPPETS)}`,
    snippet: pick(SNIPPETS),
    url: `https://example.com/${uid('u')}`,
    published_at: iso(-rand(0, 24 * 3600_000)),
    collected_at: iso(-rand(0, 3600_000)),
    sentiment_score: s,
    sentiment_confidence: round(rand(0.55, 0.98), 2),
    model_name: cat === 'news' ? 'ProsusAI/finbert' : 'ElKulako/cryptobert',
    sample_size: Math.floor(rand(10, 300)),
    derived_decision: hasDecision
      ? { direction: s > 0 ? 'long' : 'short', opportunity_score: Math.floor(rand(60, 92)), correlation_id: uid('corr') }
      : null,
  };
}).sort((a, b) => +new Date(b.collected_at) - +new Date(a.collected_at));

export function queryContent(q: ContentQuery): ContentPage {
  const limit = q.limit ?? 50;
  const offset = q.offset ?? 0;
  let items = _pool;
  if (q.category && q.category !== 'all') items = items.filter((x) => x.source_category === q.category);
  if (q.symbol) items = items.filter((x) => x.symbols.includes(q.symbol!.toUpperCase()));
  if (q.sentiment && q.sentiment !== 'all') {
    items = items.filter((x) =>
      q.sentiment === 'pos' ? x.sentiment_score > 0.15 : q.sentiment === 'neg' ? x.sentiment_score < -0.15 : Math.abs(x.sentiment_score) <= 0.15);
  }
  if (q.q) { const t = q.q.toLowerCase(); items = items.filter((x) => (x.title + x.snippet).toLowerCase().includes(t)); }
  return { items: items.slice(offset, offset + limit), total: items.length, offset, limit };
}

export function contentStats(): DataStats {
  const social = _pool.filter((x) => x.source_category === 'social').length;
  const news = _pool.filter((x) => x.source_category === 'news').length;
  const market = _pool.filter((x) => x.source_category === 'market').length;
  const avg = round(_pool.reduce((s, x) => s + x.sentiment_score, 0) / _pool.length, 2);
  const hours = Array.from({ length: 12 }, (_, h) => 11 - h);
  const volume_series = hours.map((h) => ({
    hour: `${String((new Date().getHours() - h + 24) % 24).padStart(2, '0')}h`,
    social: Math.round(rand(40, 160)), news: Math.round(rand(10, 60)), market: Math.round(rand(20, 80)),
  }));
  const sentiment_series = hours.map((h) => ({
    hour: `${String((new Date().getHours() - h + 24) % 24).padStart(2, '0')}h`, sentiment: round(rand(-0.3, 0.6), 2),
  }));
  const srcCount: Record<string, number> = {};
  _pool.forEach((x) => { srcCount[x.platform] = (srcCount[x.platform] ?? 0) + 1; });
  const top_sources = Object.entries(srcCount).map(([source, count]) => ({ source, count })).sort((a, b) => b.count - a.count).slice(0, 6);
  const menCount: Record<string, number> = {};
  _pool.forEach((x) => x.symbols.forEach((s) => { menCount[s] = (menCount[s] ?? 0) + 1; }));
  const mentions = Object.entries(menCount).map(([symbol, count]) => ({ symbol, count })).sort((a, b) => b.count - a.count).slice(0, 8);
  return { total_24h: _pool.length, social_24h: social, news_24h: news, market_24h: market,
    avg_sentiment: avg, volume_series, sentiment_series, top_sources, mentions, updated_at: new Date().toISOString() };
}
```

- [ ] **Step 2: content route.**

```ts
import { NextResponse } from 'next/server';
import { queryContent } from '@/lib/mock/content';
import type { ContentCategory } from '@/lib/types/content';

export async function GET(req: Request) {
  const p = new URL(req.url).searchParams;
  return NextResponse.json(queryContent({
    category: (p.get('category') as ContentCategory) ?? 'all',
    symbol: p.get('symbol') ?? undefined,
    q: p.get('q') ?? undefined,
    sentiment: (p.get('sentiment') as 'all' | 'pos' | 'neg' | 'neu') ?? 'all',
    limit: p.get('limit') ? Number(p.get('limit')) : 50,
    offset: p.get('offset') ? Number(p.get('offset')) : 0,
  }));
}
```

- [ ] **Step 3: stats route.**

```ts
import { NextResponse } from 'next/server';
import { contentStats } from '@/lib/mock/content';

export async function GET() { return NextResponse.json(contentStats()); }
```

- [ ] **Step 4: endpoint.** Add to `endpoints.ts`:

```ts
export const dataApi = {
  content: (q: ContentQuery = {}) =>
    api.get<ContentPage>('/data/content', { params: q }).then((r) => r.data),
  stats: () => api.get<DataStats>('/data/stats').then((r) => r.data),
};
```

- [ ] **Step 5: Verify** — `npm run typecheck`/`lint` clean. Manual: `fetch('/api/mock/data/stats')` returns splits; `fetch('/api/mock/data/content?category=news&limit=5')` returns ≤5 news items.
- [ ] **Step 6: Commit**
```bash
git add frontend/src/lib/mock/content.ts frontend/src/app/api/mock/data frontend/src/lib/api/endpoints.ts
git commit -m "feat(data): content+stats mock generators, BFF routes, dataApi"
```

---

## Task 8: Command Center — page skeleton + KpiTicker

**Files:**
- Create: `frontend/src/components/command/KpiTicker.tsx`
- Create: `frontend/src/app/(app)/command/page.tsx`

- [ ] **Step 1: KpiTicker.** Horizontal strip, scrollable on mobile. Reuse `HealthDot`.

```tsx
'use client';
import { Box, Stack, Typography } from '@mui/material';
import { pnlColor } from '@/theme/theme';
import { HealthDot } from '@/components/systems/common';
import type { Portfolio, RiskExposure, TradingStatus } from '@/lib/types/domain';

function Cell({ label, value, color }: { label: string; value: React.ReactNode; color?: string }) {
  return (
    <Box sx={{ px: 2, py: 1, borderRight: '1px solid', borderColor: 'divider', minWidth: 120, flex: '1 0 auto' }}>
      <Typography className="mono" sx={{ fontWeight: 800, fontSize: 16, color: color ?? 'text.primary', lineHeight: 1.1 }}>{value}</Typography>
      <Typography variant="caption" color="text.secondary" sx={{ textTransform: 'uppercase', letterSpacing: 0.5, fontSize: 9.5 }}>{label}</Typography>
    </Box>
  );
}

export function KpiTicker({ portfolio, status, exposure, eventsPerMin }: {
  portfolio?: Portfolio; status?: TradingStatus; exposure?: RiskExposure; eventsPerMin: number;
}) {
  const live = status?.mode === 'live';
  return (
    <Box className="cmi-glass reveal" sx={{ borderRadius: 3, display: 'flex', overflowX: 'auto', '&::-webkit-scrollbar': { height: 5 } }}>
      <Cell label="Portefeuille" value={portfolio ? `$${portfolio.total_value_usd.toLocaleString('fr-FR')}` : '—'} color={pnlColor(portfolio?.pnl_24h_pct ?? 0)} />
      <Cell label="PnL jour" value={portfolio ? `${portfolio.realized_pnl_24h_usd >= 0 ? '+' : ''}$${portfolio.realized_pnl_24h_usd}` : '—'} color={pnlColor(portfolio?.realized_pnl_24h_usd ?? 0)} />
      <Cell label="Exposition" value={exposure ? `${exposure.total_exposure_pct}%` : '—'} />
      <Cell label="Positions" value={exposure?.open_positions ?? '—'} />
      <Cell label={live ? 'LIVE · auto' : 'PAPER · auto'} value={status?.auto_trading_enabled ? 'AUTO' : 'MANUEL'} color={live ? '#ff5370' : '#ffb547'} />
      <Cell label="evt/min" value={eventsPerMin} color="#4d9fff" />
      <Box sx={{ px: 2, py: 1, display: 'flex', alignItems: 'center', gap: 1 }}>
        <HealthDot status={status?.trading_enabled ? 'healthy' : 'down'} />
        <Typography variant="caption" color="text.secondary">Kraken · Kafka</Typography>
      </Box>
    </Box>
  );
}
```

- [ ] **Step 2: Page skeleton** wiring queries + WS rate counter (copy the `useEventsPerMin` pattern from the old dashboard).

```tsx
'use client';
import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { Box, Stack } from '@mui/material';
import { PageHeader } from '@/components/common';
import { portfolioApi, tradingApi, riskApi } from '@/lib/api/endpoints';
import { useEventSubscription } from '@/lib/ws/WebSocketProvider';
import { KpiTicker } from '@/components/command/KpiTicker';

function useEventsPerMin() {
  const [rate, setRate] = useState(0);
  const ts = useState<number[]>(() => [])[0];
  useEventSubscription([], () => {
    const now = Date.now(); ts.push(now);
    while (ts.length && now - ts[0] > 60_000) ts.shift();
    setRate(ts.length);
  });
  return rate;
}

export default function CommandCenterPage() {
  const eventsPerMin = useEventsPerMin();
  const portfolio = useQuery({ queryKey: ['portfolio'], queryFn: portfolioApi.get, refetchInterval: 6000 });
  const status = useQuery({ queryKey: ['trading', 'status'], queryFn: tradingApi.status, refetchInterval: 10000 });
  const exposure = useQuery({ queryKey: ['risk', 'exposure'], queryFn: riskApi.exposure, refetchInterval: 8000 });

  return (
    <Box sx={{ p: { xs: 2, md: 3 } }}>
      <PageHeader title="Command Center" subtitle="Supervision temps réel — décisions, exécution, marché & santé" />
      <KpiTicker portfolio={portfolio.data} status={status.data} exposure={exposure.data} eventsPerMin={eventsPerMin} />
      {/* Zones added in Tasks 9–11 */}
    </Box>
  );
}
```

- [ ] **Step 3: Verify** — dev server, login, navigate `/command`, screenshot: ticker shows live values, no console error. `typecheck`/`lint` clean.
- [ ] **Step 4: Commit**
```bash
git add frontend/src/components/command/KpiTicker.tsx frontend/src/app/\(app\)/command/page.tsx
git commit -m "feat(command): page skeleton + live KPI ticker"
```

---

## Task 9: Command Center — LiveEventStream + LivePipeline + DecisionTraceDrawer

**Files:**
- Create: `frontend/src/components/command/LiveEventStream.tsx`
- Create: `frontend/src/components/command/DecisionTraceDrawer.tsx`
- Modify: `frontend/src/app/(app)/command/page.tsx`
- Reuse: `components/systems/PipelineFlow.tsx`

- [ ] **Step 1: DecisionTraceDrawer** — right drawer, full-screen on mobile, fetches `traceApi.get(cid)`.

```tsx
'use client';
import { useQuery } from '@tanstack/react-query';
import { Box, Drawer, Stack, Typography, IconButton, Chip } from '@mui/material';
import CloseIcon from '@mui/icons-material/Close';
import { traceApi } from '@/lib/api/endpoints';
import { HealthDot } from '@/components/systems/common';

const KIND_LABEL: Record<string, string> = { price: 'Prix', sentiment: 'Sentiment', analysis: 'Haiku', decision: 'Sonnet', risk: 'Risque', order: 'Ordre' };

export function DecisionTraceDrawer({ correlationId, onClose }: { correlationId: string | null; onClose: () => void }) {
  const { data } = useQuery({ queryKey: ['trace', correlationId], queryFn: () => traceApi.get(correlationId!), enabled: !!correlationId });
  return (
    <Drawer anchor="right" open={!!correlationId} onClose={onClose}
      PaperProps={{ sx: { width: { xs: '100%', sm: 460 }, bgcolor: 'rgba(8,11,20,0.92)', backdropFilter: 'blur(16px)', p: 2.5 } }}>
      <Stack direction="row" justifyContent="space-between" alignItems="center" sx={{ mb: 2 }}>
        <Box>
          <Typography variant="overline" color="text.secondary">Trace décisionnelle</Typography>
          <Typography variant="h6">{data?.symbol ?? '…'}</Typography>
        </Box>
        <IconButton onClick={onClose}><CloseIcon /></IconButton>
      </Stack>
      <Stack spacing={0}>
        {data?.stages.map((s, i) => (
          <Box key={s.kind} sx={{ pl: 2, pb: 2, borderLeft: '2px solid', borderColor: s.reached ? 'primary.main' : 'divider', position: 'relative' }}>
            <Box sx={{ position: 'absolute', left: -6, top: 2, width: 10, height: 10, borderRadius: '50%', bgcolor: s.reached ? 'primary.main' : 'text.disabled' }} />
            <Stack direction="row" spacing={1} alignItems="center">
              <Chip size="small" label={`${i + 1} · ${KIND_LABEL[s.kind]}`} sx={{ height: 20, fontSize: 10 }} />
              {s.at && <Typography variant="caption" color="text.secondary" className="mono">{new Date(s.at).toLocaleTimeString('fr-FR')}</Typography>}
            </Stack>
            <Typography variant="body2" sx={{ mt: 0.5 }}>{s.summary}</Typography>
            <Stack direction="row" flexWrap="wrap" useFlexGap sx={{ gap: 0.5, mt: 0.5 }}>
              {Object.entries(s.detail).map(([k, v]) => (
                <Chip key={k} size="small" variant="outlined" className="mono" label={`${k}: ${v}`} sx={{ height: 18, fontSize: 9.5 }} />
              ))}
            </Stack>
          </Box>
        ))}
      </Stack>
    </Drawer>
  );
}
```

- [ ] **Step 2: LiveEventStream** — buffers WS events, clickable rows emit their `correlation_id`.

```tsx
'use client';
import { useRef, useState } from 'react';
import { Box, Stack, Typography, Chip } from '@mui/material';
import { SectionCard } from '@/components/systems/common';
import { useEventSubscription } from '@/lib/ws/WebSocketProvider';
import type { CmiEvent } from '@/lib/types/events';

const LABEL: Record<string, string> = { PriceEvent: 'PRIX', SentimentEvent: 'SENTIMENT', AnalysisEvent: 'HAIKU', DecisionEvent: 'SONNET', RiskApprovedEvent: 'RISQUE', OrderExecutedEvent: 'ORDRE', PositionChangedEvent: 'POSITION', PortfolioChangedEvent: 'PORTF.' };

export function LiveEventStream({ onSelect }: { onSelect: (cid: string) => void }) {
  const [events, setEvents] = useState<CmiEvent[]>([]);
  const buf = useRef<CmiEvent[]>([]);
  useEventSubscription(
    ['PriceEvent','SentimentEvent','AnalysisEvent','DecisionEvent','RiskApprovedEvent','OrderExecutedEvent','PositionChangedEvent'],
    (e) => { buf.current = [e, ...buf.current].slice(0, 40); setEvents(buf.current); });
  return (
    <SectionCard title="Flux temps réel" subtitle="Clique un événement → sa lignée décisionnelle" noPad>
      <Box sx={{ maxHeight: 340, overflowY: 'auto' }}>
        {events.map((e) => (
          <Stack key={e.event_id} direction="row" justifyContent="space-between" alignItems="center" onClick={() => e.correlation_id && onSelect(e.correlation_id)}
            className="flash-up" sx={{ px: 2, py: 0.75, cursor: e.correlation_id ? 'pointer' : 'default', borderBottom: '1px solid', borderColor: 'divider', '&:hover': { bgcolor: 'rgba(77,159,255,0.08)' } }}>
            <Stack direction="row" spacing={1} alignItems="center">
              <Typography variant="caption" className="mono" color="text.secondary">{new Date(e.occurred_at ?? Date.now()).toLocaleTimeString('fr-FR')}</Typography>
              <Chip size="small" label={LABEL[e.event_type] ?? e.event_type} sx={{ height: 18, fontSize: 9 }} />
              <Typography variant="body2" sx={{ fontWeight: 600 }}>{e.symbol}</Typography>
            </Stack>
            {e.correlation_id && <Typography variant="caption" color="primary.main">trace →</Typography>}
          </Stack>
        ))}
        {!events.length && <Typography variant="body2" color="text.secondary" sx={{ p: 3, textAlign: 'center' }}>En attente d'événements…</Typography>}
      </Box>
    </SectionCard>
  );
}
```

- [ ] **Step 3: Wire into page** — add pipeline + stream + drawer state below the ticker:

```tsx
// add imports
import { PipelineFlow } from '@/components/systems/PipelineFlow';
import { LiveEventStream } from '@/components/command/LiveEventStream';
import { DecisionTraceDrawer } from '@/components/command/DecisionTraceDrawer';
import { systemsApi } from '@/lib/api/endpoints';
// inside component:
const [traceCid, setTraceCid] = useState<string | null>(null);
const systems = useQuery({ queryKey: ['systems','overview'], queryFn: systemsApi.overview, refetchInterval: 8000 });
// in JSX after <KpiTicker/>:
<Box sx={{ mt: 2, display: 'grid', gap: 2, gridTemplateColumns: { xs: '1fr', lg: '2fr 1fr' }, alignItems: 'start' }}>
  <Stack spacing={2}>
    <Box className="cmi-glass reveal" sx={{ borderRadius: 3, p: 2 }}>
      {systems.data && <PipelineFlow stages={systems.data.pipeline} />}
    </Box>
    <LiveEventStream onSelect={setTraceCid} />
  </Stack>
  <Stack spacing={2}>{/* AiDecisionFeed etc. added Task 10 */}</Stack>
</Box>
<DecisionTraceDrawer correlationId={traceCid} onClose={() => setTraceCid(null)} />
```

- [ ] **Step 4: Verify** — dev server, login, `/command`: events stream in; click one with "trace →" → drawer opens with 6 stages. `typecheck`/`lint` clean.
- [ ] **Step 5: Commit**
```bash
git add frontend/src/components/command frontend/src/app/\(app\)/command/page.tsx
git commit -m "feat(command): live event stream, pipeline strip, decision-trace drawer"
```

---

## Task 10: Command Center — right rail + middle panels

**Files:**
- Create: `frontend/src/components/command/{AiDecisionFeed,LivePnlPanel,MarketHeatPanel,GuardrailPanel,HealthRail}.tsx`
- Modify: `frontend/src/app/(app)/command/page.tsx`

- [ ] **Step 1: AiDecisionFeed** — subscribes to Analysis/Decision events; long/short pill + confidence.

```tsx
'use client';
import { useRef, useState } from 'react';
import { Stack, Typography, Chip, Box } from '@mui/material';
import { SectionCard } from '@/components/systems/common';
import { useEventSubscription } from '@/lib/ws/WebSocketProvider';
import type { AnalysisEvent, DecisionEvent, CmiEvent } from '@/lib/types/events';

type Item = { id: string; symbol: string; worker: 'Haiku' | 'Sonnet'; dir?: 'long' | 'short'; conf: number; score: number };
export function AiDecisionFeed() {
  const [items, setItems] = useState<Item[]>([]);
  const buf = useRef<Item[]>([]);
  useEventSubscription(['AnalysisEvent', 'DecisionEvent'], (e: CmiEvent) => {
    const it: Item = e.event_type === 'DecisionEvent'
      ? { id: e.event_id!, symbol: e.symbol, worker: 'Sonnet', dir: (e as DecisionEvent).direction, conf: (e as DecisionEvent).confidence, score: (e as DecisionEvent).opportunity_score }
      : { id: e.event_id!, symbol: e.symbol, worker: 'Haiku', conf: (e as AnalysisEvent).confidence, score: (e as AnalysisEvent).opportunity_score };
    buf.current = [it, ...buf.current].slice(0, 20); setItems(buf.current);
  });
  return (
    <SectionCard title="Fil décisions IA" subtitle="Haiku triage · Sonnet senior" accent="#a78bfa" noPad>
      <Box sx={{ maxHeight: 300, overflowY: 'auto' }}>
        {items.map((it) => (
          <Stack key={it.id} direction="row" justifyContent="space-between" alignItems="center" sx={{ px: 2, py: 0.75, borderBottom: '1px solid', borderColor: 'divider' }}>
            <Stack direction="row" spacing={1} alignItems="center">
              <Typography variant="body2" sx={{ fontWeight: 700 }}>{it.symbol}</Typography>
              <Chip size="small" label={it.worker} sx={{ height: 18, fontSize: 9, bgcolor: 'rgba(167,139,250,0.16)', color: '#d3c4ff' }} />
            </Stack>
            <Chip size="small" label={it.dir ? `${it.dir.toUpperCase()} ${Math.round(it.conf * 100)}%` : `score ${it.score}`}
              color={it.dir === 'long' ? 'success' : it.dir === 'short' ? 'error' : 'default'} variant={it.dir ? 'filled' : 'outlined'} sx={{ height: 20, fontSize: 10 }} />
          </Stack>
        ))}
        {!items.length && <Typography variant="body2" color="text.secondary" sx={{ p: 3, textAlign: 'center' }}>En attente…</Typography>}
      </Box>
    </SectionCard>
  );
}
```

- [ ] **Step 2: LivePnlPanel** — positions with live PnL + sparkline (reuse `Sparkline`).

```tsx
'use client';
import { useQuery } from '@tanstack/react-query';
import { Stack, Typography, Box } from '@mui/material';
import { SectionCard, Sparkline } from '@/components/systems/common';
import { portfolioApi } from '@/lib/api/endpoints';
import { pnlColor } from '@/theme/theme';

export function LivePnlPanel() {
  const { data } = useQuery({ queryKey: ['positions'], queryFn: portfolioApi.positions, refetchInterval: 5000 });
  return (
    <SectionCard title="PnL & positions live" accent="#26d07c" noPad>
      <Box sx={{ p: 1.5 }}>
        {(data ?? []).map((p) => (
          <Stack key={p.position_id} direction="row" justifyContent="space-between" alignItems="center" sx={{ py: 0.75 }}>
            <Typography variant="body2"><b>{p.symbol}</b> {p.direction} · {p.quantity}</Typography>
            <Stack direction="row" spacing={1} alignItems="center">
              <Sparkline data={Array.from({ length: 10 }, (_, i) => p.unrealized_pnl_usd * (0.6 + 0.08 * i))} color={pnlColor(p.unrealized_pnl_usd)} width={60} height={20} />
              <Typography className="mono" sx={{ fontWeight: 700, color: pnlColor(p.unrealized_pnl_usd), minWidth: 64, textAlign: 'right' }}>
                {p.unrealized_pnl_usd >= 0 ? '+' : ''}${p.unrealized_pnl_usd}
              </Typography>
            </Stack>
          </Stack>
        ))}
      </Box>
    </SectionCard>
  );
}
```

- [ ] **Step 3: MarketHeatPanel** — token heat chips (reuse `marketApi.tokens`).

```tsx
'use client';
import { useQuery } from '@tanstack/react-query';
import { Box, Chip } from '@mui/material';
import { SectionCard } from '@/components/systems/common';
import { marketApi } from '@/lib/api/endpoints';

export function MarketHeatPanel() {
  const { data } = useQuery({ queryKey: ['market', 'tokens'], queryFn: marketApi.tokens, refetchInterval: 7000 });
  return (
    <SectionCard title="Heat marché" accent="#22d3ee">
      <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 0.75 }}>
        {(data ?? []).slice(0, 16).map((t) => (
          <Chip key={t.symbol} size="small" label={`${t.symbol} ${t.price_change_pct_24h >= 0 ? '▲' : '▼'}${Math.abs(t.price_change_pct_24h)}`}
            color={t.price_change_pct_24h >= 0 ? 'success' : 'error'} variant="outlined" className="mono" sx={{ height: 22, fontSize: 10.5 }} />
        ))}
      </Box>
    </SectionCard>
  );
}
```

- [ ] **Step 4: GuardrailPanel** — exposure gauge + daily loss + kill-switch (reuse `MiniBar`, `riskApi.exposure`, `tradingApi.status`).

```tsx
'use client';
import { useQuery } from '@tanstack/react-query';
import { Stack, Typography, Box, Chip } from '@mui/material';
import { SectionCard, MiniBar } from '@/components/systems/common';
import { riskApi, tradingApi } from '@/lib/api/endpoints';

export function GuardrailPanel() {
  const exp = useQuery({ queryKey: ['risk', 'exposure'], queryFn: riskApi.exposure, refetchInterval: 8000 });
  const st = useQuery({ queryKey: ['trading', 'status'], queryFn: tradingApi.status, refetchInterval: 10000 });
  const e = exp.data;
  return (
    <SectionCard title="Garde-fous" accent="#ffb547">
      <Stack spacing={1.5}>
        <Box>
          <Stack direction="row" justifyContent="space-between"><Typography variant="caption" color="text.secondary">Exposition</Typography>
            <Typography variant="caption" className="mono">{e?.total_exposure_pct ?? 0}% / {e?.max_exposure_pct ?? 80}%</Typography></Stack>
          <MiniBar pct={e ? (e.total_exposure_pct / e.max_exposure_pct) * 100 : 0} color="#ffb547" />
        </Box>
        <Box>
          <Stack direction="row" justifyContent="space-between"><Typography variant="caption" color="text.secondary">Perte jour</Typography>
            <Typography variant="caption" className="mono">${e?.daily_loss_usd ?? 0} / ${e?.daily_loss_limit_usd ?? 0}</Typography></Stack>
          <MiniBar pct={e ? (e.daily_loss_usd / e.daily_loss_limit_usd) * 100 : 0} color="#ff5370" />
        </Box>
        <Chip label={st.data?.trading_enabled ? 'Kill-switch OFF' : 'KILL-SWITCH ON'} color={st.data?.trading_enabled ? 'success' : 'error'}
          size="small" variant="outlined" sx={{ alignSelf: 'flex-start' }} />
      </Stack>
    </SectionCard>
  );
}
```

- [ ] **Step 5: HealthRail** — compact service health + link to /systems (reuse `systemsApi.overview`, `HealthDot`).

```tsx
'use client';
import Link from 'next/link';
import { useQuery } from '@tanstack/react-query';
import { Box, Stack, Typography } from '@mui/material';
import { SectionCard, HealthDot } from '@/components/systems/common';
import { systemsApi } from '@/lib/api/endpoints';

export function HealthRail() {
  const { data } = useQuery({ queryKey: ['systems', 'overview'], queryFn: systemsApi.overview, refetchInterval: 8000 });
  return (
    <SectionCard title="Santé technique" accent="#22d3ee"
      actions={<Typography component={Link} href="/systems" variant="caption" sx={{ color: 'primary.main' }}>voir tout →</Typography>}>
      <Box sx={{ display: 'flex', flexWrap: 'wrap', gap: 1 }}>
        {(data?.services ?? []).slice(0, 10).map((s) => (
          <Stack key={s.id} direction="row" spacing={0.75} alignItems="center" sx={{ minWidth: 130 }}>
            <HealthDot status={s.status} size={8} />
            <Typography variant="caption" noWrap>{s.name}</Typography>
          </Stack>
        ))}
      </Box>
    </SectionCard>
  );
}
```

- [ ] **Step 6: Assemble into page** — fill the right `Stack` and add a middle grid:

```tsx
// imports
import { AiDecisionFeed } from '@/components/command/AiDecisionFeed';
import { LivePnlPanel } from '@/components/command/LivePnlPanel';
import { MarketHeatPanel } from '@/components/command/MarketHeatPanel';
import { GuardrailPanel } from '@/components/command/GuardrailPanel';
import { HealthRail } from '@/components/command/HealthRail';
// right Stack content:
<AiDecisionFeed />
<GuardrailPanel />
// after the 2-col grid, add:
<Box sx={{ mt: 2, display: 'grid', gap: 2, gridTemplateColumns: { xs: '1fr', md: '1fr 1fr' } }}>
  <LivePnlPanel /><MarketHeatPanel />
</Box>
<Box sx={{ mt: 2 }}><HealthRail /></Box>
```

- [ ] **Step 7: Verify** — `/command` shows all zones populated; `typecheck`/`lint` clean; no console errors.
- [ ] **Step 8: Commit**
```bash
git add frontend/src/components/command frontend/src/app/\(app\)/command/page.tsx
git commit -m "feat(command): AI feed, live PnL, market heat, guardrails, health rail"
```

---

## Task 11: Command Center — responsive pass

**Files:**
- Modify: `frontend/src/app/(app)/command/page.tsx` (only if overflow found)

- [ ] **Step 1: Playwright mobile check.** Resize 390×844, login, navigate `/command`, run:
```js
() => ({ vw: document.documentElement.clientWidth, sw: document.documentElement.scrollWidth, overflow: document.documentElement.scrollWidth > document.documentElement.clientWidth + 1 })
```
Expected: `overflow: false`. (The `AppShell` main already has `minWidth:0` + `overflowX:hidden` from the Systems fix.)

- [ ] **Step 2: If overflow**, find offender via the per-element scan (same script used for Systems) and constrain it (add `minWidth:0` to the offending grid/flex child or `overflowX:auto` to its scroll container). Re-check until `overflow:false`.

- [ ] **Step 3: Screenshot** desktop (1440) + mobile (390); confirm clean. Commit if changes:
```bash
git add frontend/src/app/\(app\)/command/page.tsx
git commit -m "fix(command): mobile responsive pass"
```

---

## Task 12: Data Explorer — page skeleton + DataStatsRow

**Files:**
- Create: `frontend/src/components/data/DataStatsRow.tsx`
- Create: `frontend/src/app/(app)/data/page.tsx`

- [ ] **Step 1: DataStatsRow** — 5 tiles (reuse the SummaryRow tile pattern; simpler here).

```tsx
'use client';
import { Box, Card, CardContent, Typography } from '@mui/material';
import type { DataStats } from '@/lib/types/content';

function Tile({ label, value, accent }: { label: string; value: React.ReactNode; accent: string }) {
  return (
    <Card className="reveal"><CardContent>
      <Typography className="mono" sx={{ fontWeight: 800, fontSize: 22, color: accent }}>{value}</Typography>
      <Typography variant="caption" color="text.secondary" sx={{ textTransform: 'uppercase', letterSpacing: 0.6 }}>{label}</Typography>
    </CardContent></Card>
  );
}
export function DataStatsRow({ s }: { s?: DataStats }) {
  const f = (n?: number) => (n == null ? '—' : n.toLocaleString('fr-FR'));
  return (
    <Box sx={{ display: 'grid', gap: 2, gridTemplateColumns: { xs: '1fr 1fr', md: 'repeat(5,1fr)' } }}>
      <Tile label="Items / 24h" value={f(s?.total_24h)} accent="#e8ecf5" />
      <Tile label="Social" value={f(s?.social_24h)} accent="#a78bfa" />
      <Tile label="News" value={f(s?.news_24h)} accent="#4d9fff" />
      <Tile label="Marché" value={f(s?.market_24h)} accent="#22d3ee" />
      <Tile label="Sentiment moyen" value={s ? s.avg_sentiment.toFixed(2) : '—'} accent={s && s.avg_sentiment >= 0 ? '#26d07c' : '#ff5370'} />
    </Box>
  );
}
```

- [ ] **Step 2: Page skeleton.**

```tsx
'use client';
import { useQuery } from '@tanstack/react-query';
import { Box } from '@mui/material';
import { PageHeader } from '@/components/common';
import { dataApi } from '@/lib/api/endpoints';
import { DataStatsRow } from '@/components/data/DataStatsRow';

export default function DataExplorerPage() {
  const stats = useQuery({ queryKey: ['data', 'stats'], queryFn: dataApi.stats, refetchInterval: 15000 });
  return (
    <Box sx={{ p: { xs: 2, md: 3 } }}>
      <PageHeader title="Data Explorer" subtitle="Tout le contenu collecté — news, social & marché — scoré et relié aux décisions" />
      <DataStatsRow s={stats.data} />
      {/* charts (Task 13), filters+table (Task 14) */}
    </Box>
  );
}
```

- [ ] **Step 3: Verify** — `/data` renders stats; `typecheck`/`lint` clean.
- [ ] **Step 4: Commit**
```bash
git add frontend/src/components/data/DataStatsRow.tsx frontend/src/app/\(app\)/data/page.tsx
git commit -m "feat(data): Data Explorer skeleton + stats row"
```

---

## Task 13: Data Explorer — charts

**Files:**
- Create: `frontend/src/components/data/{IngestionVolumeChart,SentimentTrendChart,TopSourcesChart,MentionsChart}.tsx`
- Modify: `frontend/src/app/(app)/data/page.tsx`

- [ ] **Step 1: IngestionVolumeChart** — stacked bars (Recharts).

```tsx
'use client';
import { ResponsiveContainer, BarChart, Bar, XAxis, Tooltip } from 'recharts';
import { SectionCard } from '@/components/systems/common';
import type { DataStats } from '@/lib/types/content';

export function IngestionVolumeChart({ s }: { s?: DataStats }) {
  return (
    <SectionCard title="Volume ingéré / heure" subtitle="empilé par catégorie" accent="#4d9fff">
      <ResponsiveContainer width="100%" height={160}>
        <BarChart data={s?.volume_series ?? []}>
          <XAxis dataKey="hour" tick={{ fill: '#8b97b0', fontSize: 10 }} axisLine={false} tickLine={false} />
          <Tooltip contentStyle={{ background: '#141b2b', border: '1px solid rgba(255,255,255,0.1)', borderRadius: 8 }} />
          <Bar dataKey="social" stackId="a" fill="#a78bfa" />
          <Bar dataKey="news" stackId="a" fill="#4d9fff" />
          <Bar dataKey="market" stackId="a" fill="#22d3ee" radius={[3, 3, 0, 0]} />
        </BarChart>
      </ResponsiveContainer>
    </SectionCard>
  );
}
```

- [ ] **Step 2: SentimentTrendChart** — area line.

```tsx
'use client';
import { ResponsiveContainer, AreaChart, Area, XAxis, Tooltip, ReferenceLine } from 'recharts';
import { SectionCard } from '@/components/systems/common';
import type { DataStats } from '@/lib/types/content';

export function SentimentTrendChart({ s }: { s?: DataStats }) {
  return (
    <SectionCard title="Tendance du sentiment" accent="#26d07c">
      <ResponsiveContainer width="100%" height={160}>
        <AreaChart data={s?.sentiment_series ?? []}>
          <defs><linearGradient id="sg" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stopColor="#26d07c" stopOpacity={0.4} /><stop offset="100%" stopColor="#26d07c" stopOpacity={0} /></linearGradient></defs>
          <XAxis dataKey="hour" tick={{ fill: '#8b97b0', fontSize: 10 }} axisLine={false} tickLine={false} />
          <ReferenceLine y={0} stroke="rgba(255,255,255,0.15)" />
          <Tooltip contentStyle={{ background: '#141b2b', border: '1px solid rgba(255,255,255,0.1)', borderRadius: 8 }} />
          <Area type="monotone" dataKey="sentiment" stroke="#26d07c" fill="url(#sg)" strokeWidth={2} />
        </AreaChart>
      </ResponsiveContainer>
    </SectionCard>
  );
}
```

- [ ] **Step 3: TopSourcesChart + MentionsChart** — horizontal bars.

```tsx
// TopSourcesChart.tsx
'use client';
import { ResponsiveContainer, BarChart, Bar, XAxis, YAxis, Tooltip } from 'recharts';
import { SectionCard } from '@/components/systems/common';
import type { DataStats } from '@/lib/types/content';
export function TopSourcesChart({ s }: { s?: DataStats }) {
  return (
    <SectionCard title="Top sources" accent="#22d3ee">
      <ResponsiveContainer width="100%" height={160}>
        <BarChart layout="vertical" data={s?.top_sources ?? []} margin={{ left: 10 }}>
          <XAxis type="number" hide /><YAxis type="category" dataKey="source" width={72} tick={{ fill: '#8b97b0', fontSize: 10 }} axisLine={false} tickLine={false} />
          <Tooltip contentStyle={{ background: '#141b2b', border: '1px solid rgba(255,255,255,0.1)', borderRadius: 8 }} />
          <Bar dataKey="count" fill="#22d3ee" radius={[0, 4, 4, 0]} />
        </BarChart>
      </ResponsiveContainer>
    </SectionCard>
  );
}
```
```tsx
// MentionsChart.tsx — identical structure, dataKey "count", YAxis dataKey "symbol", fill "#4d9fff", title "Mentions / token"
'use client';
import { ResponsiveContainer, BarChart, Bar, XAxis, YAxis, Tooltip } from 'recharts';
import { SectionCard } from '@/components/systems/common';
import type { DataStats } from '@/lib/types/content';
export function MentionsChart({ s }: { s?: DataStats }) {
  return (
    <SectionCard title="Mentions / token" accent="#4d9fff">
      <ResponsiveContainer width="100%" height={160}>
        <BarChart layout="vertical" data={s?.mentions ?? []} margin={{ left: 10 }}>
          <XAxis type="number" hide /><YAxis type="category" dataKey="symbol" width={48} tick={{ fill: '#8b97b0', fontSize: 10 }} axisLine={false} tickLine={false} />
          <Tooltip contentStyle={{ background: '#141b2b', border: '1px solid rgba(255,255,255,0.1)', borderRadius: 8 }} />
          <Bar dataKey="count" fill="#4d9fff" radius={[0, 4, 4, 0]} />
        </BarChart>
      </ResponsiveContainer>
    </SectionCard>
  );
}
```

- [ ] **Step 4: Add charts grid to page** below `DataStatsRow`:

```tsx
import { IngestionVolumeChart } from '@/components/data/IngestionVolumeChart';
import { SentimentTrendChart } from '@/components/data/SentimentTrendChart';
import { TopSourcesChart } from '@/components/data/TopSourcesChart';
import { MentionsChart } from '@/components/data/MentionsChart';
// JSX:
<Box sx={{ mt: 2, display: 'grid', gap: 2, gridTemplateColumns: { xs: '1fr', md: '2fr 1fr' } }}>
  <IngestionVolumeChart s={stats.data} />
  <SentimentTrendChart s={stats.data} />
</Box>
<Box sx={{ mt: 2, display: 'grid', gap: 2, gridTemplateColumns: { xs: '1fr', md: '1fr 1fr' } }}>
  <TopSourcesChart s={stats.data} />
  <MentionsChart s={stats.data} />
</Box>
```

- [ ] **Step 5: Verify** — charts render with data; `typecheck`/`lint` clean.
- [ ] **Step 6: Commit**
```bash
git add frontend/src/components/data frontend/src/app/\(app\)/data/page.tsx
git commit -m "feat(data): ingestion volume, sentiment trend, top sources, mentions charts"
```

---

## Task 14: Data Explorer — filter bar, firehose table, detail drawer

**Files:**
- Create: `frontend/src/components/data/DataFilterBar.tsx`
- Create: `frontend/src/components/data/ContentFirehoseTable.tsx`
- Create: `frontend/src/components/data/ContentDetailDrawer.tsx`
- Modify: `frontend/src/app/(app)/data/page.tsx`

- [ ] **Step 1: DataFilterBar** — controlled filters.

```tsx
'use client';
import { Box, ToggleButton, ToggleButtonGroup, TextField, MenuItem } from '@mui/material';
import type { ContentCategory, ContentQuery } from '@/lib/types/content';

export function DataFilterBar({ value, onChange }: { value: ContentQuery; onChange: (q: ContentQuery) => void }) {
  return (
    <Box sx={{ display: 'flex', gap: 1.5, flexWrap: 'wrap', alignItems: 'center' }}>
      <ToggleButtonGroup size="small" exclusive value={value.category ?? 'all'} onChange={(_, v) => v && onChange({ ...value, category: v as ContentCategory })}>
        <ToggleButton value="all">Tout</ToggleButton><ToggleButton value="social">Social</ToggleButton>
        <ToggleButton value="news">News</ToggleButton><ToggleButton value="market">Marché</ToggleButton>
      </ToggleButtonGroup>
      <TextField size="small" placeholder="🔍 recherche" value={value.q ?? ''} onChange={(e) => onChange({ ...value, q: e.target.value })} sx={{ minWidth: 180 }} />
      <TextField size="small" placeholder="token (BTC)" value={value.symbol ?? ''} onChange={(e) => onChange({ ...value, symbol: e.target.value || undefined })} sx={{ width: 130 }} />
      <TextField size="small" select label="Sentiment" value={value.sentiment ?? 'all'} onChange={(e) => onChange({ ...value, sentiment: e.target.value as ContentQuery['sentiment'] })} sx={{ width: 140 }}>
        <MenuItem value="all">Tous</MenuItem><MenuItem value="pos">Positif</MenuItem><MenuItem value="neu">Neutre</MenuItem><MenuItem value="neg">Négatif</MenuItem>
      </TextField>
    </Box>
  );
}
```

- [ ] **Step 2: ContentFirehoseTable** — dense rows, sentiment pill, derived-decision cell, row click.

```tsx
'use client';
import { Box, Chip, Stack, Typography } from '@mui/material';
import type { RawContentItem } from '@/lib/types/content';

function SentPill({ v }: { v: number }) {
  const c = v > 0.15 ? 'success' : v < -0.15 ? 'error' : 'default';
  return <Chip size="small" color={c} variant="outlined" className="mono" label={v.toFixed(2)} sx={{ height: 18, fontSize: 9.5 }} />;
}
export function ContentFirehoseTable({ items, onSelect }: { items: RawContentItem[]; onSelect: (i: RawContentItem) => void }) {
  return (
    <Box>
      {items.map((i) => (
        <Stack key={i.id} direction="row" alignItems="center" spacing={1.5} onClick={() => onSelect(i)}
          sx={{ px: 1, py: 0.9, borderBottom: '1px solid', borderColor: 'divider', cursor: 'pointer', '&:hover': { bgcolor: 'rgba(77,159,255,0.08)' } }}>
          <Typography variant="caption" className="mono" color="text.secondary" sx={{ width: 44 }}>{new Date(i.collected_at).toLocaleTimeString('fr-FR', { hour: '2-digit', minute: '2-digit' })}</Typography>
          <Typography variant="caption" sx={{ color: '#9fe9f5', width: 84 }} noWrap>{i.platform}</Typography>
          <Chip size="small" label={i.source_category} variant="outlined" sx={{ height: 17, fontSize: 9 }} />
          <Typography variant="caption" sx={{ width: 44, fontWeight: 700 }} noWrap>{i.symbols[0] ?? '—'}</Typography>
          <Typography variant="body2" sx={{ flex: 1, minWidth: 0 }} noWrap>{i.title}</Typography>
          <SentPill v={i.sentiment_score} />
          {i.derived_decision?.direction
            ? <Chip size="small" color={i.derived_decision.direction === 'long' ? 'success' : 'error'} label={`→ ${i.derived_decision.direction.toUpperCase()}`} sx={{ height: 18, fontSize: 9 }} />
            : <Typography variant="caption" color="text.secondary" sx={{ width: 54, textAlign: 'right' }}>—</Typography>}
        </Stack>
      ))}
      {!items.length && <Typography variant="body2" color="text.secondary" sx={{ p: 3, textAlign: 'center' }}>Aucun résultat.</Typography>}
    </Box>
  );
}
```

- [ ] **Step 3: ContentDetailDrawer** — full content + sentiment breakdown + link to trace.

```tsx
'use client';
import { Drawer, Box, Stack, Typography, Chip, IconButton, Divider } from '@mui/material';
import CloseIcon from '@mui/icons-material/Close';
import type { RawContentItem } from '@/lib/types/content';

export function ContentDetailDrawer({ item, onOpenTrace, onClose }: { item: RawContentItem | null; onOpenTrace: (cid: string) => void; onClose: () => void }) {
  return (
    <Drawer anchor="right" open={!!item} onClose={onClose}
      PaperProps={{ sx: { width: { xs: '100%', sm: 440 }, bgcolor: 'rgba(8,11,20,0.92)', backdropFilter: 'blur(16px)', p: 2.5 } }}>
      {item && (
        <>
          <Stack direction="row" justifyContent="space-between" alignItems="center" sx={{ mb: 2 }}>
            <Typography variant="overline" color="text.secondary">{item.platform} · {item.source_category}</Typography>
            <IconButton onClick={onClose}><CloseIcon /></IconButton>
          </Stack>
          <Typography variant="h6" sx={{ mb: 1 }}>{item.title}</Typography>
          <Typography variant="body2" color="text.secondary" sx={{ mb: 2 }}>{item.snippet}</Typography>
          <Stack direction="row" spacing={1} sx={{ mb: 2 }} flexWrap="wrap" useFlexGap>
            {item.symbols.map((s) => <Chip key={s} size="small" label={s} />)}
          </Stack>
          <Divider sx={{ my: 2 }} />
          <Typography variant="overline" color="text.secondary">Sentiment</Typography>
          <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap sx={{ mb: 2 }}>
            <Chip size="small" className="mono" label={`score ${item.sentiment_score.toFixed(2)}`} />
            <Chip size="small" className="mono" label={`conf ${Math.round(item.sentiment_confidence * 100)}%`} />
            <Chip size="small" className="mono" label={item.model_name} />
            <Chip size="small" className="mono" label={`n=${item.sample_size}`} />
          </Stack>
          {item.derived_decision?.correlation_id && (
            <Chip color="primary" variant="outlined" label="Voir la trace décisionnelle →"
              onClick={() => onOpenTrace(item.derived_decision!.correlation_id!)} />
          )}
        </>
      )}
    </Drawer>
  );
}
```

- [ ] **Step 4: Wire into page** — filters state, content query, table, detail drawer + reuse `DecisionTraceDrawer`.

```tsx
// imports
import { useState } from 'react';
import { DataFilterBar } from '@/components/data/DataFilterBar';
import { ContentFirehoseTable } from '@/components/data/ContentFirehoseTable';
import { ContentDetailDrawer } from '@/components/data/ContentDetailDrawer';
import { DecisionTraceDrawer } from '@/components/command/DecisionTraceDrawer';
import { SectionCard } from '@/components/systems/common';
import type { ContentQuery, RawContentItem } from '@/lib/types/content';
// state + query
const [filters, setFilters] = useState<ContentQuery>({ category: 'all', sentiment: 'all', limit: 60 });
const [detail, setDetail] = useState<RawContentItem | null>(null);
const [traceCid, setTraceCid] = useState<string | null>(null);
const content = useQuery({ queryKey: ['data', 'content', filters], queryFn: () => dataApi.content(filters), refetchInterval: 10000 });
// JSX after charts:
<Box sx={{ mt: 2 }}>
  <SectionCard title="Firehose — contenu collecté" subtitle={`${content.data?.total ?? 0} items`} accent="#a78bfa" noPad
    actions={<Box sx={{ display: { xs: 'none', md: 'block' } }}><DataFilterBar value={filters} onChange={setFilters} /></Box>}>
    <Box sx={{ p: 1.5, display: { xs: 'block', md: 'none' } }}><DataFilterBar value={filters} onChange={setFilters} /></Box>
    <Box sx={{ maxHeight: 520, overflowY: 'auto', overflowX: 'auto' }}>
      <Box sx={{ minWidth: 640 }}><ContentFirehoseTable items={content.data?.items ?? []} onSelect={setDetail} /></Box>
    </Box>
  </SectionCard>
</Box>
<ContentDetailDrawer item={detail} onClose={() => setDetail(null)} onOpenTrace={(cid) => { setDetail(null); setTraceCid(cid); }} />
<DecisionTraceDrawer correlationId={traceCid} onClose={() => setTraceCid(null)} />
```

- [ ] **Step 5: Verify** — dev server: filters narrow rows; row click → detail drawer; "voir la trace" → trace drawer. `typecheck`/`lint` clean.
- [ ] **Step 6: Commit**
```bash
git add frontend/src/components/data frontend/src/app/\(app\)/data/page.tsx
git commit -m "feat(data): filter bar, firehose table, detail drawer with trace link"
```

---

## Task 15: Data Explorer — responsive pass

**Files:**
- Modify: `frontend/src/app/(app)/data/page.tsx` (only if overflow found)

- [ ] **Step 1: Playwright mobile check** at 390×844 on `/data` — assert `scrollWidth === clientWidth`. The firehose table is intentionally horizontally scrollable *inside its card* (`minWidth:640`), which must not overflow the page.
- [ ] **Step 2: Fix any offender** (same technique as Task 11). Re-check.
- [ ] **Step 3: Screenshots** 1440 + 390; commit if changed:
```bash
git add frontend/src/app/\(app\)/data/page.tsx
git commit -m "fix(data): mobile responsive pass"
```

---

## Task 16: Remove Dashboard, global verification & cleanup

**Files:**
- Delete: `frontend/src/app/(app)/dashboard/page.tsx`
- Verify: whole app

- [ ] **Step 1: Delete the old dashboard.**
```bash
git rm frontend/src/app/\(app\)/dashboard/page.tsx
```
If `dashboard/` becomes empty, ensure no other files remain. (Redirect from `/dashboard`→`/command` is already in `next.config.mjs`.)

- [ ] **Step 2: Grep for stale references** to removed routes/pages:
Run: search for `'/dashboard'`, `'/portfolio'`, `'/risk'` in `frontend/src`. Any remaining `Link href` to those still work via redirects, but update obvious in-app links to point at `/command` / `/capital` for cleanliness. Leave the `/portfolio` and `/risk` **page files** in place (Task 3's Capital page imports them); only the nav + dashboard change.

- [ ] **Step 3: Full verify.**
Run: `cd frontend && npm run typecheck` → pass. `npm run lint` → clean.
- [ ] **Step 4: Playwright end-to-end sweep** (dev server, login):
  - `/` redirects to `/command`; `/dashboard`→`/command`; `/portfolio`→`/capital`; `/risk`→`/capital`.
  - `/command`, `/data`, `/capital`, `/market`, `/trading`, `/systems`, `/settings` all render with no console errors.
  - Both new pages: `scrollWidth === clientWidth` at 390px.
  - Command Center: click a "trace →" event → drawer with 6 stages.
  - Data Explorer: category filter + token filter narrow results; detail drawer opens.
- [ ] **Step 5: Final commit.**
```bash
git add -A
git commit -m "feat: remove Dashboard (absorbed by Command Center); finalize IA"
```

---

## Self-Review (completed by plan author)

- **Spec coverage:** IA changes → Tasks 1,2,16. Command Center (A) → 8,9,10,11. Decision Trace (B) → 6,9 (drawer),5 (correlated chains). Data Explorer → 12,13,14,15. Capital merge → 3. Systèmes demotion → 1. Dashboard removal → 16. Types/mock/endpoints → 4,5,6,7. Design-system reuse → every component reuses `components/systems/common.tsx`. Responsiveness → 11,15,16. ✅ all covered.
- **Placeholders:** none — every code step contains full code. UI copy is concrete.
- **Type consistency:** `RawContentItem.source_category`, `DataStats.volume_series`, `ContentQuery`, `DecisionTrace.stages[].kind`, `dataApi.content/stats`, `traceApi.get` are used identically across tasks. `SectionCard`/`HealthDot`/`Sparkline`/`MiniBar` signatures match `components/systems/common.tsx` as built.
- **Note:** Task 3 reuses `PortfolioPage`/`RiskPage` components; if visual doubling appears, inline their bodies (flagged in-task). This is the one place requiring a judgment call during execution.
