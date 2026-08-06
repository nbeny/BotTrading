'use client';

import { useCallback } from 'react';
import { usePathname, useRouter, useSearchParams } from 'next/navigation';

/** Pilote le Decision Inspector global via ?decision=<id>, en préservant les
 *  autres search params (?token= sur /market notamment). */
export function useDecisionParam() {
  const router = useRouter();
  const pathname = usePathname();
  const params = useSearchParams();
  const decisionId = params.get('decision');

  const open = useCallback(
    (id: string) => {
      const next = new URLSearchParams(params);
      next.set('decision', id);
      router.push(`${pathname}?${next.toString()}`, { scroll: false });
    },
    [params, pathname, router],
  );

  const close = useCallback(() => {
    const next = new URLSearchParams(params);
    next.delete('decision');
    const qs = next.toString();
    router.push(qs ? `${pathname}?${qs}` : pathname, { scroll: false });
  }, [params, pathname, router]);

  return { decisionId, open, close };
}
