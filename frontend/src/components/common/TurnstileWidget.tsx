'use client';

/**
 * Cloudflare Turnstile widget for the login form.
 *
 * Rendered *explicitly* (`?render=explicit`) rather than via the `cf-turnstile`
 * auto-scan class: React owns the DOM node, and auto-scan would re-inject a
 * second iframe on every remount. The explicit API gives us a widget id, which
 * is what `reset()` needs — a Turnstile token is single-use, so after a failed
 * login the old token is already spent and the parent must ask for a new one.
 *
 * Deliberately no npm dependency: the whole integration is one script tag and
 * three callbacks, and the vendor script has to come from Cloudflare's domain
 * anyway (the token is minted by that iframe, not by us).
 */

import { forwardRef, useEffect, useImperativeHandle, useRef } from 'react';
import { Box } from '@mui/material';
import { TURNSTILE_SITE_KEY } from '@/lib/config';

const SCRIPT_ID = 'cf-turnstile-script';
const SCRIPT_SRC = 'https://challenges.cloudflare.com/turnstile/v0/api.js?render=explicit';

interface TurnstileApi {
  render: (el: HTMLElement, opts: Record<string, unknown>) => string;
  reset: (widgetId?: string) => void;
  remove: (widgetId: string) => void;
}

declare global {
  interface Window {
    turnstile?: TurnstileApi;
  }
}

/** Shared across mounts so the vendor script is fetched at most once. */
let scriptPromise: Promise<void> | null = null;

function loadTurnstile(): Promise<void> {
  if (typeof window === 'undefined') return Promise.resolve();
  if (window.turnstile) return Promise.resolve();
  if (scriptPromise) return scriptPromise;

  scriptPromise = new Promise<void>((resolve, reject) => {
    const existing = document.getElementById(SCRIPT_ID);
    const script = (existing as HTMLScriptElement | null) ?? document.createElement('script');
    script.id = SCRIPT_ID;
    script.src = SCRIPT_SRC;
    script.async = true;
    script.defer = true;
    script.addEventListener('load', () => resolve());
    script.addEventListener('error', () => {
      // Allow a later mount to retry (the user may have unblocked the domain).
      scriptPromise = null;
      reject(new Error('Turnstile script could not be loaded'));
    });
    if (!existing) document.head.appendChild(script);
  });
  return scriptPromise;
}

export interface TurnstileHandle {
  /** Discard the spent token and re-challenge. Call after a failed submit. */
  reset: () => void;
}

interface TurnstileWidgetProps {
  /** Receives the fresh token, or `null` when it expires / errors out. */
  onToken: (token: string | null) => void;
  /** Called when the vendor script itself never loads (blocked, offline). */
  onScriptError?: () => void;
}

export const TurnstileWidget = forwardRef<TurnstileHandle, TurnstileWidgetProps>(
  function TurnstileWidget({ onToken, onScriptError }, ref) {
    const containerRef = useRef<HTMLDivElement | null>(null);
    const widgetIdRef = useRef<string | null>(null);
    // Kept in refs so re-renders of the parent never re-run the render effect
    // (a re-render would otherwise tear down the iframe mid-challenge).
    const onTokenRef = useRef(onToken);
    const onScriptErrorRef = useRef(onScriptError);
    onTokenRef.current = onToken;
    onScriptErrorRef.current = onScriptError;

    useImperativeHandle(ref, () => ({
      reset: () => {
        onTokenRef.current(null);
        if (widgetIdRef.current && window.turnstile) {
          window.turnstile.reset(widgetIdRef.current);
        }
      },
    }));

    useEffect(() => {
      if (!TURNSTILE_SITE_KEY) return;
      let cancelled = false;

      loadTurnstile()
        .then(() => {
          if (cancelled || !containerRef.current || !window.turnstile) return;
          widgetIdRef.current = window.turnstile.render(containerRef.current, {
            sitekey: TURNSTILE_SITE_KEY,
            theme: 'dark',
            action: 'login',
            callback: (token: string) => onTokenRef.current(token),
            'expired-callback': () => onTokenRef.current(null),
            'error-callback': () => onTokenRef.current(null),
          });
        })
        .catch(() => {
          if (!cancelled) onScriptErrorRef.current?.();
        });

      return () => {
        cancelled = true;
        if (widgetIdRef.current && window.turnstile) {
          window.turnstile.remove(widgetIdRef.current);
          widgetIdRef.current = null;
        }
      };
    }, []);

    if (!TURNSTILE_SITE_KEY) return null;
    return <Box ref={containerRef} sx={{ display: 'flex', justifyContent: 'center' }} />;
  },
);
