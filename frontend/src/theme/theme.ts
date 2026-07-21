'use client';

import { createTheme, alpha } from '@mui/material/styles';

/**
 * Dark "trading terminal" theme. Tuned for dense financial dashboards:
 * high-contrast numerics, green/red PnL semantics, calm slate surfaces.
 */
const green = '#26d07c';
const red = '#ff5370';
const amber = '#ffb547';
const accent = '#5b8def';

export const theme = createTheme({
  palette: {
    mode: 'dark',
    primary: { main: accent },
    secondary: { main: '#8b5cf6' },
    success: { main: green },
    error: { main: red },
    warning: { main: amber },
    info: { main: accent },
    background: {
      default: '#0b0e14',
      paper: '#121722',
    },
    divider: alpha('#ffffff', 0.08),
    text: {
      primary: '#e6e9ef',
      secondary: '#94a0b8',
    },
  },
  shape: { borderRadius: 12 },
  typography: {
    fontFamily:
      '"Inter", "Segoe UI", system-ui, -apple-system, sans-serif',
    h4: { fontWeight: 700, letterSpacing: -0.5 },
    h5: { fontWeight: 700, letterSpacing: -0.3 },
    h6: { fontWeight: 700 },
    subtitle2: { fontWeight: 600 },
    button: { textTransform: 'none', fontWeight: 600 },
  },
  components: {
    MuiCssBaseline: {
      styleOverrides: {
        body: { scrollbarColor: '#2a3242 transparent' },
        '::-webkit-scrollbar': { width: 8, height: 8 },
        '::-webkit-scrollbar-thumb': {
          background: '#2a3242',
          borderRadius: 8,
        },
      },
    },
    MuiPaper: {
      styleOverrides: {
        root: {
          backgroundImage: 'none',
          border: `1px solid ${alpha('#ffffff', 0.06)}`,
        },
      },
    },
    MuiCard: {
      defaultProps: { elevation: 0 },
      styleOverrides: {
        root: {
          backgroundImage: 'none',
          border: `1px solid ${alpha('#ffffff', 0.06)}`,
        },
      },
    },
    MuiButton: {
      defaultProps: { disableElevation: true },
    },
    MuiChip: {
      styleOverrides: { root: { fontWeight: 600 } },
    },
    MuiTableCell: {
      styleOverrides: {
        root: { borderColor: alpha('#ffffff', 0.06) },
        head: { color: '#94a0b8', fontWeight: 600, fontSize: 12 },
      },
    },
  },
});

export const pnlColor = (v: number) => (v > 0 ? green : v < 0 ? red : '#94a0b8');
