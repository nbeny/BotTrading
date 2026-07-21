'use client';

import {
  Avatar,
  Box,
  Button,
  Card,
  CardContent,
  Chip,
  Divider,
  Stack,
  Tooltip,
  Typography,
} from '@mui/material';
import LogoutIcon from '@mui/icons-material/Logout';
import { useAuth } from '@/lib/auth/AuthProvider';
import { permissionsFor, ROLE_LABEL } from '@/lib/auth/rbac';
import type { Permission } from '@/lib/auth/types';

const PERMISSION_LABEL: Record<Permission, string> = {
  'trading.toggle_auto': 'Activer / désactiver le trading automatique',
  'trading.switch_mode': 'Basculer entre les modes dry_run, demo et live',
  'trading.approve_opportunity': 'Valider ou rejeter les opportunités en attente',
  'trading.manual_order': 'Passer des ordres manuels',
  'trading.close_position': 'Clôturer des positions ouvertes',
  'trading.adjust_sltp': 'Ajuster Stop-Loss et Take-Profit',
  'risk.view': 'Consulter le tableau de bord Risque',
  'settings.edit': 'Modifier les paramètres et préférences',
};

export function SessionPanel() {
  const { user, can, logout } = useAuth();

  if (!user) return null;

  const perms = permissionsFor(user.role);
  const roleLabel = ROLE_LABEL[user.role];
  const initials = user.name
    .split(' ')
    .map((w) => w[0])
    .join('')
    .toUpperCase()
    .slice(0, 2);

  return (
    <Card>
      <CardContent>
        <Typography variant="subtitle1" fontWeight={700} gutterBottom>
          Session
        </Typography>

        <Stack direction={{ xs: 'column', sm: 'row' }} spacing={2} alignItems="flex-start">
          <Avatar sx={{ width: 52, height: 52, bgcolor: 'primary.main', fontWeight: 800 }}>
            {initials}
          </Avatar>
          <Box sx={{ flex: 1 }}>
            <Typography variant="body1" fontWeight={700}>
              {user.name}
            </Typography>
            <Typography variant="body2" color="text.secondary">
              {user.email}
            </Typography>
            <Stack direction="row" spacing={1} sx={{ mt: 1 }} alignItems="center">
              <Chip label={roleLabel} size="small" color="primary" variant="filled" />
              <Typography variant="caption" color="text.disabled">
                ID: {user.id}
              </Typography>
            </Stack>
          </Box>
          <Tooltip title={!can('settings.edit') ? 'Permission requise' : ''}>
            <span>
              <Button
                variant="outlined"
                color="error"
                startIcon={<LogoutIcon />}
                onClick={logout}
                size="small"
              >
                Déconnexion
              </Button>
            </span>
          </Tooltip>
        </Stack>

        <Divider sx={{ my: 2 }} />

        <Typography variant="caption" color="text.secondary" sx={{ textTransform: 'uppercase', letterSpacing: 0.6 }}>
          Permissions du rôle &quot;{roleLabel}&quot;
        </Typography>

        <Stack spacing={0.5} sx={{ mt: 1 }}>
          {(Object.keys(PERMISSION_LABEL) as Permission[]).map((p) => {
            const granted = perms.has(p);
            return (
              <Stack key={p} direction="row" spacing={1} alignItems="center">
                <Typography
                  variant="caption"
                  sx={{
                    color: granted ? 'success.main' : 'text.disabled',
                    fontWeight: 600,
                    minWidth: 14,
                  }}
                >
                  {granted ? '✓' : '✗'}
                </Typography>
                <Typography
                  variant="caption"
                  sx={{ color: granted ? 'text.primary' : 'text.disabled' }}
                >
                  {PERMISSION_LABEL[p]}
                </Typography>
              </Stack>
            );
          })}
        </Stack>
      </CardContent>
    </Card>
  );
}
