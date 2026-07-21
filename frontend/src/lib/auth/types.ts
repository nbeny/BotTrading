/** Auth + RBAC domain. */

export type Role = 'admin' | 'operator' | 'viewer';

/**
 * Permissions gate every mutating / sensitive action in the terminal.
 * RBAC maps roles -> permission sets (see `rbac.ts`).
 */
export type Permission =
  | 'trading.toggle_auto'
  | 'trading.switch_mode'
  | 'trading.approve_opportunity'
  | 'trading.manual_order'
  | 'trading.close_position'
  | 'trading.adjust_sltp'
  | 'risk.view'
  | 'settings.edit';

export interface User {
  id: string;
  email: string;
  name: string;
  role: Role;
}

export interface AuthTokens {
  access_token: string;
  refresh_token?: string;
  token_type?: string;
}

export interface JwtClaims {
  sub: string;
  email?: string;
  name?: string;
  role?: Role;
  exp: number;
  iat?: number;
}

export interface AuthState {
  user: User | null;
  accessToken: string | null;
  status: 'loading' | 'authenticated' | 'unauthenticated';
}
