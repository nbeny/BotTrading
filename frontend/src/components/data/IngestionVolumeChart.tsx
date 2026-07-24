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
