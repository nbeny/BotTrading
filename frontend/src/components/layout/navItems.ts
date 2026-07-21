import DashboardIcon from '@mui/icons-material/SpaceDashboard';
import InsightsIcon from '@mui/icons-material/Insights';
import CandlestickChartIcon from '@mui/icons-material/CandlestickChart';
import AccountBalanceWalletIcon from '@mui/icons-material/AccountBalanceWallet';
import ShieldIcon from '@mui/icons-material/GppMaybe';
import SettingsIcon from '@mui/icons-material/Settings';
import type { SvgIconComponent } from '@mui/icons-material';

export interface NavItem {
  href: string;
  label: string;
  icon: SvgIconComponent;
  description: string;
}

export const NAV_ITEMS: NavItem[] = [
  { href: '/dashboard', label: 'Dashboard', icon: DashboardIcon, description: 'Vue temps réel' },
  { href: '/market', label: 'Market Intelligence', icon: InsightsIcon, description: 'Tokens & IA' },
  { href: '/trading', label: 'Trading', icon: CandlestickChartIcon, description: 'Contrôle & ordres' },
  { href: '/portfolio', label: 'Portefeuille', icon: AccountBalanceWalletIcon, description: 'Positions & PnL' },
  { href: '/risk', label: 'Risque', icon: ShieldIcon, description: 'Exposition & alertes' },
  { href: '/settings', label: 'Paramètres', icon: SettingsIcon, description: 'Session & RBAC' },
];
