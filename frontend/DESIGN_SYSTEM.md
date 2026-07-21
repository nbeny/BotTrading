# CMI Terminal — Design System Reference (for contributors)

All imports use the `@/` alias → `frontend/src/`. Every component that uses hooks,
state, MUI interactive elements, react-query, or browser APIs MUST start with `'use client';`.

## Layout rule
Do NOT use MUI `Grid`. Use `Box` with CSS grid for responsive layouts:
```tsx
<Box sx={{ display: 'grid', gap: 2, gridTemplateColumns: { xs: '1fr', sm: '1fr 1fr', lg: 'repeat(4, 1fr)' } }}>
```
Use MUI `Stack` for vertical/horizontal flow. Wrap sections in MUI `Card` + `CardContent`.

## Types — `@/lib/types/domain`
`Portfolio, Position, Trade, Opportunity, MarketToken, NewsItem, WorkerDecision,`
`RiskExposure, AssetExposure, RiskLimit, RiskAlert, PricePoint, TradingStatus, TradingMode, AlertLevel`
Events: `@/lib/types/events` (`CmiEvent`, `PriceEvent`, `AnalysisEvent`, `DecisionEvent`, `RiskApprovedEvent`, ...).

## Data API — `@/lib/api/endpoints`
- `portfolioApi.get()`, `.positions()`, `.trades(limit?)`, `.history(range?)`
- `marketApi.tokens()`, `.token(symbol)`, `.prices(symbol, range?)`, `.news(limit?)`, `.decisions(limit?)`, `.signals(limit?)`
- `tradingApi.status()`, `.setAutoTrading(enabled)`, `.setMode(mode)`, `.opportunities(status?)`,
  `.approveOpportunity(id)`, `.rejectOpportunity(id, reason?)`, `.placeOrder(ManualOrderInput)`,
  `.closePosition(positionId)`, `.adjustSlTp(positionId, AdjustSlTpInput)`
- `riskApi.exposure()`, `.limits()`, `.alerts(limit?)`
- Types `ManualOrderInput`, `AdjustSlTpInput` are exported from the same module.
- `apiErrorMessage(err, fallback?)` from `@/lib/api/client`.

Use TanStack Query: `useQuery({ queryKey, queryFn, refetchInterval })` and `useMutation`.
Get the client via `useQueryClient()` to `invalidateQueries` after mutations.

## Formatting — `@/lib/format`
`fmtUsd, fmtUsdCompact, fmtPct, fmtNum, fmtScore, fmtTime, fmtDateTime, fmtRelative(iso, now), scoreColor(score)`
Color helper: `pnlColor(v)` from `@/theme/theme` (green/red/grey).

## UI components — `@/components/common`
`PageHeader({title, subtitle?, actions?})`, `StatCard({label, value, delta?, deltaFormat?, icon?, footnote?, accent?})`,
`DeltaText({value, suffix?, format?, variant?})`, `ScoreChip({score})`, `DirectionChip({direction})`,
`SentimentChip({score})`, `EmptyState({message})`.

## Charts — `@/components/charts/PriceAreaChart`
`<PriceAreaChart data={PricePoint[]} height={260} color="#5b8def" dataKey="price" />`
For other charts use `recharts` directly (BarChart, LineChart, PieChart, RadialBar). Dark tooltip style:
`contentStyle={{ background:'#121722', border:'1px solid rgba(255,255,255,0.1)', borderRadius:12, fontSize:12 }}`.

## Realtime
- `<LiveFeed height title />` from `@/components/realtime/LiveFeed`.
- `useWebSocket()` → `{ status, feed, subscribe, lastEventAt }` from `@/lib/ws/WebSocketProvider`.
- `useEventSubscription(types: EventType[], (event, msg) => void)` — react to live events (e.g. update local state).

## Auth / RBAC — `@/lib/auth/AuthProvider`
`const { user, can } = useAuth();` — gate mutating UI with `can('trading.approve_opportunity')` etc.
Permissions: `trading.toggle_auto`, `trading.switch_mode`, `trading.approve_opportunity`,
`trading.manual_order`, `trading.close_position`, `trading.adjust_sltp`, `settings.edit`.
Disable (don't hide) actions the role lacks, with a tooltip "Permission requise".

## Tables
`@mui/x-data-grid` `DataGrid` is available for dense tables, or plain MUI `Table` for small ones.

## Tone
Dark trading-terminal aesthetic, dense but legible, French UI labels, tabular-nums (`className="mono"`) for numbers.
