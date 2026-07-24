'use client';

import { createTheme, alpha } from '@mui/material/styles';

/**
 * Dark "deep-space trading terminal" theme. Tuned for dense financial +
 * infrastructure dashboards: high-contrast numerics, green/red PnL semantics,
 * electric azure/cyan accents on calm near-black surfaces. Paired in
 * globals.css with Sora (display) / Manrope (body) / IBM Plex Mono (numerics)
 * and an atmospheric CSS backdrop.
 */
const green = '#26d07c';
const red = '#ff5370';
const amber = '#ffb547';
const azure = '#4d9fff';
const cyan = '#22d3ee';
const violet = '#a78bfa';

export const palettePalette = { green, red, amber, azure, cyan, violet };

export const theme = createTheme({
  palette: {
    mode: 'dark',
    primary: { main: azure },
    secondary: { main: violet },
    success: { main: green },
    error: { main: red },
    warning: { main: amber },
    info: { main: cyan },
    background: {
      default: '#05070d',
      paper: '#0d1220',
    },
    divider: alpha('#ffffff', 0.07),
    text: {
      primary: '#e8ecf5',
      secondary: '#8b97b0',
    },
  },
  shape: { borderRadius: 14 },
  typography: {
    fontFamily: '"Manrope", "Segoe UI", system-ui, -apple-system, sans-serif',
    h3: { fontFamily: '"Sora", sans-serif', fontWeight: 800, letterSpacing: -1 },
    h4: { fontFamily: '"Sora", sans-serif', fontWeight: 700, letterSpacing: -0.6 },
    h5: { fontFamily: '"Sora", sans-serif', fontWeight: 700, letterSpacing: -0.4 },
    h6: { fontFamily: '"Sora", sans-serif', fontWeight: 700, letterSpacing: -0.2 },
    subtitle1: { fontWeight: 600 },
    subtitle2: { fontWeight: 600 },
    overline: { fontWeight: 700, letterSpacing: 1.4, fontSize: 10.5 },
    button: { textTransform: 'none', fontWeight: 600 },
  },
  components: {
    MuiCssBaseline: {
      styleOverrides: {
        body: { scrollbarColor: '#232c3d transparent' },
      },
    },
    MuiPaper: {
      styleOverrides: {
        root: {
          backgroundImage: 'none',
          border: `1px solid ${alpha('#ffffff', 0.07)}`,
          backgroundColor: alpha('#0d1220', 0.72),
          backdropFilter: 'blur(10px)',
        },
      },
    },
    MuiCard: {
      defaultProps: { elevation: 0 },
      styleOverrides: {
        root: {
          backgroundImage:
            'linear-gradient(180deg, rgba(255,255,255,0.028), rgba(255,255,255,0.006))',
          backgroundColor: alpha('#0d1220', 0.72),
          border: `1px solid ${alpha('#ffffff', 0.07)}`,
          backdropFilter: 'blur(10px)',
          transition: 'border-color 160ms ease, transform 160ms ease',
        },
      },
    },
    MuiButton: {
      defaultProps: { disableElevation: true },
      styleOverrides: {
        root: { borderRadius: 10 },
        containedPrimary: {
          boxShadow: `0 0 0 1px ${alpha(azure, 0.4)}, 0 6px 20px ${alpha(azure, 0.25)}`,
        },
      },
    },
    MuiChip: {
      styleOverrides: {
        root: { fontWeight: 600, borderRadius: 8 },
        outlined: { borderColor: alpha('#ffffff', 0.14) },
      },
    },
    MuiTooltip: {
      styleOverrides: {
        tooltip: {
          backgroundColor: '#141b2b',
          border: `1px solid ${alpha('#ffffff', 0.1)}`,
          fontSize: 12,
          borderRadius: 8,
        },
        arrow: { color: '#141b2b' },
      },
    },
    MuiTableCell: {
      styleOverrides: {
        root: { borderColor: alpha('#ffffff', 0.06) },
        head: {
          color: '#8b97b0',
          fontWeight: 700,
          fontSize: 11,
          letterSpacing: 0.5,
          textTransform: 'uppercase',
        },
      },
    },
    MuiLinearProgress: {
      styleOverrides: {
        root: { borderRadius: 999, backgroundColor: alpha('#ffffff', 0.06) },
        bar: { borderRadius: 999 },
      },
    },
  },
});

export const pnlColor = (v: number) => (v > 0 ? green : v < 0 ? red : '#8b97b0');
