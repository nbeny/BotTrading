'use client';

import { useEffect, useRef, useState } from 'react';
import { useRouter } from 'next/navigation';
import {
  Alert,
  Box,
  Button,
  Card,
  CardContent,
  Chip,
  Divider,
  Stack,
  TextField,
  Typography,
} from '@mui/material';
import BoltIcon from '@mui/icons-material/Bolt';
import { useAuth } from '@/lib/auth/AuthProvider';
import { apiErrorMessage } from '@/lib/api/client';
import { TurnstileWidget, type TurnstileHandle } from '@/components/common';
import { TURNSTILE_SITE_KEY, USE_MOCK } from '@/lib/config';

const DEMO_ACCOUNTS = [
  { role: 'Admin', email: 'admin@cmi.io' },
  { role: 'Opérateur', email: 'operator@cmi.io' },
  { role: 'Observateur', email: 'viewer@cmi.io' },
];

export default function LoginPage() {
  const { login, status } = useAuth();
  const router = useRouter();
  const [email, setEmail] = useState('');
  const [password, setPassword] = useState('');
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [captchaToken, setCaptchaToken] = useState<string | null>(null);
  const captchaRef = useRef<TurnstileHandle | null>(null);
  // Set when Cloudflare's script itself never loads: the operator would
  // otherwise stare at a disabled button with no explanation.
  const [captchaBlocked, setCaptchaBlocked] = useState(false);

  const captchaRequired = Boolean(TURNSTILE_SITE_KEY);

  useEffect(() => {
    if (status === 'authenticated') router.replace('/command');
  }, [status, router]);

  const submit = async (e: React.FormEvent) => {
    e.preventDefault();
    setError(null);
    setBusy(true);
    try {
      await login(email, password, captchaToken);
      router.replace('/command');
    } catch (err) {
      setError(apiErrorMessage(err, 'Identifiants invalides'));
      // A Turnstile token is single-use — whatever failed, the one we just
      // sent is spent, so re-challenge before the operator retries.
      captchaRef.current?.reset();
    } finally {
      setBusy(false);
    }
  };

  return (
    <Box
      sx={{
        minHeight: '100vh',
        display: 'grid',
        placeItems: 'center',
        p: 2,
        background:
          'radial-gradient(1200px 600px at 20% -10%, rgba(91,141,239,0.14), transparent), radial-gradient(900px 500px at 110% 10%, rgba(139,92,246,0.12), transparent)',
      }}
    >
      <Card sx={{ width: '100%', maxWidth: 420 }}>
        <CardContent sx={{ p: 4 }}>
          <Stack direction="row" spacing={1} alignItems="center" sx={{ mb: 0.5 }}>
            <BoltIcon color="primary" />
            <Typography variant="h5" sx={{ fontWeight: 800 }}>
              CMI · Terminal
            </Typography>
          </Stack>
          <Typography variant="body2" color="text.secondary" sx={{ mb: 3 }}>
            Supervision & contrôle de la plateforme de trading algorithmique
          </Typography>

          <form onSubmit={submit}>
            <Stack spacing={2}>
              {error && <Alert severity="error">{error}</Alert>}
              <TextField
                label="Identifiant"
                type="text"
                value={email}
                onChange={(e) => setEmail(e.target.value)}
                autoComplete="username"
                fullWidth
                required
              />
              <TextField
                label="Mot de passe"
                type="password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
                autoComplete="current-password"
                fullWidth
                required
              />
              <TurnstileWidget
                ref={captchaRef}
                onToken={(t) => {
                  setCaptchaToken(t);
                  if (t) setCaptchaBlocked(false);
                }}
                onScriptError={() => setCaptchaBlocked(true)}
              />
              {captchaBlocked && (
                <Alert severity="warning">
                  Vérification anti-bot indisponible (script Cloudflare bloqué). Désactivez le
                  bloqueur de contenu puis rechargez la page.
                </Alert>
              )}
              <Button
                type="submit"
                variant="contained"
                size="large"
                disabled={busy || (captchaRequired && !captchaToken)}
                fullWidth
              >
                {busy ? 'Connexion…' : 'Se connecter'}
              </Button>
            </Stack>
          </form>

          {USE_MOCK && (
            <>
              <Divider sx={{ my: 3 }}>
                <Typography variant="caption" color="text.secondary">
                  Comptes de démonstration (mdp : demo)
                </Typography>
              </Divider>
              <Stack direction="row" spacing={1} flexWrap="wrap" useFlexGap>
                {DEMO_ACCOUNTS.map((a) => (
                  <Chip
                    key={a.email}
                    label={`${a.role}`}
                    onClick={() => {
                      setEmail(a.email);
                      setPassword('demo');
                    }}
                    variant="outlined"
                    clickable
                  />
                ))}
              </Stack>
            </>
          )}
        </CardContent>
      </Card>
    </Box>
  );
}
