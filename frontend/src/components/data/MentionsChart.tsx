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
