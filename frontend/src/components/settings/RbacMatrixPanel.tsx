'use client';

import {
  Box,
  Card,
  CardContent,
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableRow,
  Tooltip,
  Typography,
} from '@mui/material';
import CheckCircleIcon from '@mui/icons-material/CheckCircle';
import CancelIcon from '@mui/icons-material/Cancel';
import { permissionsFor, ROLE_LABEL } from '@/lib/auth/rbac';
import type { Permission, Role } from '@/lib/auth/types';

const ALL_PERMISSIONS: Permission[] = [
  'trading.toggle_auto',
  'trading.switch_mode',
  'trading.approve_opportunity',
  'trading.manual_order',
  'trading.close_position',
  'trading.adjust_sltp',
  'risk.view',
  'settings.edit',
];

const PERMISSION_SHORT_LABEL: Record<Permission, string> = {
  'trading.toggle_auto': 'Auto-trading on/off',
  'trading.switch_mode': 'Basculer paper/live',
  'trading.approve_opportunity': 'Valider opportunités',
  'trading.manual_order': 'Ordres manuels',
  'trading.close_position': 'Clôturer positions',
  'trading.adjust_sltp': 'Ajuster SL/TP',
  'risk.view': 'Vue risque',
  'settings.edit': 'Modifier paramètres',
};

const ROLES: Role[] = ['admin', 'operator', 'viewer'];

export function RbacMatrixPanel() {
  const permsMap = Object.fromEntries(
    ROLES.map((r) => [r, permissionsFor(r)]),
  ) as Record<Role, Set<Permission>>;

  return (
    <Card>
      <CardContent>
        <Typography variant="subtitle1" fontWeight={700} gutterBottom>
          RBAC — Rôles &amp; permissions
        </Typography>
        <Typography variant="caption" color="text.secondary">
          Matrice des droits par rôle. Les actions sont désactivées (non masquées) pour les rôles sans permission.
        </Typography>

        <Box sx={{ mt: 2, overflowX: 'auto' }}>
          <Table size="small">
            <TableHead>
              <TableRow>
                <TableCell sx={{ minWidth: 180 }}>Permission</TableCell>
                {ROLES.map((role) => (
                  <TableCell key={role} align="center" sx={{ fontWeight: 700 }}>
                    {ROLE_LABEL[role]}
                  </TableCell>
                ))}
              </TableRow>
            </TableHead>
            <TableBody>
              {ALL_PERMISSIONS.map((perm) => (
                <TableRow key={perm}>
                  <TableCell>
                    <Tooltip title={perm} placement="right">
                      <Typography variant="body2">
                        {PERMISSION_SHORT_LABEL[perm]}
                      </Typography>
                    </Tooltip>
                  </TableCell>
                  {ROLES.map((role) => {
                    const granted = permsMap[role].has(perm);
                    return (
                      <TableCell key={role} align="center">
                        {granted ? (
                          <CheckCircleIcon
                            sx={{ color: 'success.main', fontSize: 18 }}
                          />
                        ) : (
                          <CancelIcon
                            sx={{ color: 'text.disabled', fontSize: 18 }}
                          />
                        )}
                      </TableCell>
                    );
                  })}
                </TableRow>
              ))}
            </TableBody>
          </Table>
        </Box>
      </CardContent>
    </Card>
  );
}
