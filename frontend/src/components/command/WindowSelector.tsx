'use client';
import { ToggleButton, ToggleButtonGroup } from '@mui/material';
import type { SystemsWindow } from '@/lib/types/systems';

const WINDOWS: SystemsWindow[] = ['1h', '24h', '7d'];

export function WindowSelector({
  value,
  onChange,
}: {
  value: SystemsWindow;
  onChange: (w: SystemsWindow) => void;
}) {
  return (
    <ToggleButtonGroup
      size="small"
      exclusive
      value={value}
      onChange={(_, v) => {
        // MUI passes `null` when the already-active button is clicked (it
        // toggles off with nothing to replace it) — fall back to the current
        // value instead of letting the window collapse to nothing selected.
        if (v != null) onChange(v as SystemsWindow);
      }}
    >
      {WINDOWS.map((w) => (
        <ToggleButton key={w} value={w} sx={{ px: 1.5, fontSize: 11.5 }}>
          {w}
        </ToggleButton>
      ))}
    </ToggleButtonGroup>
  );
}
