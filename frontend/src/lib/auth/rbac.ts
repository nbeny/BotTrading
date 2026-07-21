import type { Permission, Role } from './types';

/**
 * Role -> permission matrix.
 *  - admin:    full control incl. live-mode switch and settings
 *  - operator: can supervise, validate opportunities, place/close orders
 *              in the current mode, but cannot flip paper<->live or edit settings
 *  - viewer:   read-only supervision
 */
const ROLE_PERMISSIONS: Record<Role, Permission[]> = {
  admin: [
    'trading.toggle_auto',
    'trading.switch_mode',
    'trading.approve_opportunity',
    'trading.manual_order',
    'trading.close_position',
    'trading.adjust_sltp',
    'risk.view',
    'settings.edit',
  ],
  operator: [
    'trading.toggle_auto',
    'trading.approve_opportunity',
    'trading.manual_order',
    'trading.close_position',
    'trading.adjust_sltp',
    'risk.view',
  ],
  viewer: ['risk.view'],
};

export function permissionsFor(role: Role | undefined): Set<Permission> {
  if (!role) return new Set();
  return new Set(ROLE_PERMISSIONS[role] ?? []);
}

export function hasPermission(
  role: Role | undefined,
  permission: Permission,
): boolean {
  return permissionsFor(role).has(permission);
}

export const ROLE_LABEL: Record<Role, string> = {
  admin: 'Administrateur',
  operator: 'Opérateur',
  viewer: 'Observateur',
};
