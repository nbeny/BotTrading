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
