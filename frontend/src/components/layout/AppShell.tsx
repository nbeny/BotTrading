'use client';

import { useState } from 'react';
import Link from 'next/link';
import { usePathname } from 'next/navigation';
import {
  AppBar,
  Avatar,
  Box,
  Chip,
  Divider,
  Drawer,
  IconButton,
  List,
  ListItemButton,
  ListItemIcon,
  ListItemText,
  Menu,
  MenuItem,
  Stack,
  Toolbar,
  Tooltip,
  Typography,
  useMediaQuery,
} from '@mui/material';
import { useTheme } from '@mui/material/styles';
import MenuIcon from '@mui/icons-material/Menu';
import LogoutIcon from '@mui/icons-material/Logout';
import FiberManualRecordIcon from '@mui/icons-material/FiberManualRecord';
import BoltIcon from '@mui/icons-material/Bolt';
import { NAV_ITEMS } from './navItems';
import { useAuth } from '@/lib/auth/AuthProvider';
import { useWebSocket } from '@/lib/ws/WebSocketProvider';
import { ROLE_LABEL } from '@/lib/auth/rbac';
import { useQuery } from '@tanstack/react-query';
import { tradingApi } from '@/lib/api/endpoints';

const DRAWER_WIDTH = 248;

function ConnectionBadge() {
  const { status, lastEventAt } = useWebSocket();
  const color = status === 'open' ? '#26d07c' : status === 'connecting' ? '#ffb547' : '#ff5370';
  const label = status === 'open' ? 'LIVE' : status === 'connecting' ? 'CONNEXION…' : 'HORS LIGNE';
  const fresh = lastEventAt != null && Date.now() - lastEventAt < 4000;
  return (
    <Tooltip title="Flux temps réel (WebSocket · Kafka)">
      <Stack direction="row" spacing={0.75} alignItems="center">
        <FiberManualRecordIcon
          className={status === 'open' && fresh ? 'pulse' : undefined}
          sx={{ fontSize: 12, color }}
        />
        <Typography variant="caption" sx={{ color, fontWeight: 700, letterSpacing: 0.5 }}>
          {label}
        </Typography>
      </Stack>
    </Tooltip>
  );
}

function ModeBadge() {
  const { data } = useQuery({
    queryKey: ['trading', 'status'],
    queryFn: tradingApi.status,
    refetchInterval: 15_000,
  });
  if (!data) return null;
  const live = data.mode === 'live';
  return (
    <Stack direction="row" spacing={1} alignItems="center">
      <Chip
        icon={<BoltIcon />}
        label={live ? 'LIVE TRADING' : 'PAPER TRADING'}
        color={live ? 'error' : 'default'}
        size="small"
        variant={live ? 'filled' : 'outlined'}
      />
      <Chip
        label={data.auto_trading_enabled ? 'AUTO' : 'MANUEL'}
        color={data.auto_trading_enabled ? 'success' : 'default'}
        size="small"
        variant="outlined"
      />
    </Stack>
  );
}

function SidebarContent({ onNavigate }: { onNavigate?: () => void }) {
  const pathname = usePathname();
  return (
    <Box sx={{ height: '100%', display: 'flex', flexDirection: 'column' }}>
      <Box sx={{ px: 3, py: 2.5 }}>
        <Typography variant="h6" sx={{ fontWeight: 800, letterSpacing: -0.5 }}>
          CMI<span style={{ color: '#5b8def' }}>·</span>Terminal
        </Typography>
        <Typography variant="caption" color="text.secondary">
          Crypto Market Intelligence
        </Typography>
      </Box>
      <Divider />
      <List sx={{ px: 1.5, py: 1, flex: 1 }}>
        {NAV_ITEMS.map((item) => {
          const active = pathname === item.href || pathname.startsWith(item.href + '/');
          const Icon = item.icon;
          return (
            <ListItemButton
              key={item.href}
              component={Link}
              href={item.href}
              selected={active}
              onClick={onNavigate}
              sx={{
                borderRadius: 2,
                mb: 0.5,
                '&.Mui-selected': {
                  bgcolor: 'rgba(91,141,239,0.14)',
                  '&:hover': { bgcolor: 'rgba(91,141,239,0.2)' },
                },
              }}
            >
              <ListItemIcon sx={{ minWidth: 40, color: active ? 'primary.main' : 'text.secondary' }}>
                <Icon />
              </ListItemIcon>
              <ListItemText
                primary={item.label}
                secondary={item.description}
                primaryTypographyProps={{ fontWeight: 600, fontSize: 14 }}
                secondaryTypographyProps={{ fontSize: 11 }}
              />
            </ListItemButton>
          );
        })}
      </List>
      <Divider />
      <Box sx={{ p: 2 }}>
        <Typography variant="caption" color="text.secondary">
          Kraken · Kafka · Claude Haiku/Sonnet
        </Typography>
      </Box>
    </Box>
  );
}

export function AppShell({ children }: { children: React.ReactNode }) {
  const theme = useTheme();
  const isMobile = useMediaQuery(theme.breakpoints.down('md'));
  const [mobileOpen, setMobileOpen] = useState(false);
  const [anchor, setAnchor] = useState<null | HTMLElement>(null);
  const { user, logout } = useAuth();

  return (
    <Box sx={{ display: 'flex', minHeight: '100vh' }}>
      <AppBar
        position="fixed"
        elevation={0}
        sx={{
          width: { md: `calc(100% - ${DRAWER_WIDTH}px)` },
          ml: { md: `${DRAWER_WIDTH}px` },
          bgcolor: 'rgba(11,14,20,0.7)',
          backdropFilter: 'blur(12px)',
          borderBottom: '1px solid rgba(255,255,255,0.06)',
        }}
      >
        <Toolbar sx={{ gap: 2 }}>
          {isMobile && (
            <IconButton edge="start" onClick={() => setMobileOpen(true)}>
              <MenuIcon />
            </IconButton>
          )}
          <Box sx={{ flex: 1 }}>
            <ModeBadge />
          </Box>
          <ConnectionBadge />
          <Divider orientation="vertical" flexItem sx={{ mx: 1 }} />
          <IconButton onClick={(e) => setAnchor(e.currentTarget)} size="small">
            <Avatar sx={{ width: 34, height: 34, bgcolor: 'primary.main', fontSize: 14 }}>
              {(user?.name ?? '?').slice(0, 1).toUpperCase()}
            </Avatar>
          </IconButton>
          <Menu anchorEl={anchor} open={Boolean(anchor)} onClose={() => setAnchor(null)}>
            <Box sx={{ px: 2, py: 1 }}>
              <Typography variant="subtitle2">{user?.name}</Typography>
              <Typography variant="caption" color="text.secondary">
                {user?.email}
              </Typography>
              <br />
              <Chip
                label={user ? ROLE_LABEL[user.role] : ''}
                size="small"
                color="primary"
                variant="outlined"
                sx={{ mt: 0.5 }}
              />
            </Box>
            <Divider />
            <MenuItem
              onClick={() => {
                setAnchor(null);
                logout();
              }}
            >
              <ListItemIcon>
                <LogoutIcon fontSize="small" />
              </ListItemIcon>
              Déconnexion
            </MenuItem>
          </Menu>
        </Toolbar>
      </AppBar>

      <Box component="nav" sx={{ width: { md: DRAWER_WIDTH }, flexShrink: { md: 0 } }}>
        <Drawer
          variant="temporary"
          open={mobileOpen}
          onClose={() => setMobileOpen(false)}
          ModalProps={{ keepMounted: true }}
          sx={{
            display: { xs: 'block', md: 'none' },
            '& .MuiDrawer-paper': { width: DRAWER_WIDTH, boxSizing: 'border-box' },
          }}
        >
          <SidebarContent onNavigate={() => setMobileOpen(false)} />
        </Drawer>
        <Drawer
          variant="permanent"
          open
          sx={{
            display: { xs: 'none', md: 'block' },
            '& .MuiDrawer-paper': {
              width: DRAWER_WIDTH,
              boxSizing: 'border-box',
              borderRight: '1px solid rgba(255,255,255,0.06)',
            },
          }}
        >
          <SidebarContent />
        </Drawer>
      </Box>

      <Box
        component="main"
        sx={{
          flexGrow: 1,
          width: { md: `calc(100% - ${DRAWER_WIDTH}px)` },
          p: { xs: 2, md: 3 },
          mt: 8,
        }}
      >
        {children}
      </Box>
    </Box>
  );
}
