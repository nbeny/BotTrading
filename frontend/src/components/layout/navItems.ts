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
