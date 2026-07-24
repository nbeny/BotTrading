# CMI · Web Terminal

Terminal web de **supervision et de contrôle** de la plateforme de trading
algorithmique CMI (Crypto Market Intelligence) connectée à Kraken.

Interface opérateur : superviser le marché, valider les opportunités générées par
les workers Claude (Haiku → Sonnet), piloter le moteur de trading et surveiller le
risque — le tout en temps réel.

## Stack

- **Next.js 15** (App Router, React Server + Client Components) · **React 19** · **TypeScript**
- **MUI v6** (thème « trading terminal » sombre) + **@mui/x-data-grid**
- **TanStack Query** (data fetching / cache / mutations)
- **Recharts** (courbes prix / valeur / allocation / exposition)
- **WebSocket** temps réel (Kafka → WebSocket Gateway → Next.js)
- **JWT** + **RBAC** (admin / opérateur / observateur)

## Architecture

```
  Navigateur (Next.js Terminal)
        │  REST lecture  (/api/gateway/*  → rewrite → api-gateway :8000)
        │  REST écriture (/api/control/*  → rewrite → control-api :8000, dev :8001)
        │  WS            (NEXT_PUBLIC_WS_URL → websocket-gateway :8080/ws)
        ▼
  api-gateway  (READ-ONLY : opportunities / decisions / trades)
  control-api  (auth JWT + /trading/* → control.commands → trading-engine)
  websocket-gateway ◄── Kafka (market.* / decision.* / risk.approved.* / execution.*)
```

- **Mode démo (par défaut)** : `NEXT_PUBLIC_USE_MOCK=1`. Un **BFF intégré**
  (`src/app/api/mock/*`) sert toutes les données et un **flux synthétique**
  (`src/lib/ws/mockStream.ts`) alimente le temps réel — aucune dépendance backend.
- **Mode live** : `NEXT_PUBLIC_USE_MOCK=0`. Les lectures sont proxifiées vers
  `api-gateway`, **les écritures (`/trading/*`, auth) vers `control-api`**, le
  WebSocket pointe vers `websocket-gateway`.

> ℹ️ Le **plan de contrôle est câblé pour le live** (endpoints → control-api).
> En revanche l'`api-gateway` est **lecture seule** et n'expose que
> `/api/v1/{opportunities,decisions,trades}` : les endpoints portefeuille /
> marché / risque consommés par ce terminal (voir `src/lib/api/endpoints.ts`)
> restent à ajouter côté backend pour le live ; ils sont entièrement implémentés
> par le BFF de démo.

## Démarrage

```bash
cd frontend
npm install
cp .env.local.example .env.local   # déjà en mode démo par défaut
npm run dev                         # http://localhost:3000
```

Connexion (mode démo, mot de passe `demo`) :

| Compte              | Rôle        | Droits                                             |
| ------------------- | ----------- | -------------------------------------------------- |
| `admin@cmi.io`      | Admin       | tout, y compris bascule Live et paramètres         |
| `operator@cmi.io`   | Opérateur   | valider, ordres, fermer, ajuster SL/TP (pas Live)  |
| `viewer@cmi.io`     | Observateur | lecture seule                                      |

## Routes

| Route        | Contenu                                                             |
| ------------ | ------------------------------------------------------------------- |
| `/dashboard` | KPIs portefeuille, PnL live, signaux, scores, alertes, flux temps réel |
| `/market`    | Tokens surveillés, prix/volume/liquidité/sentiment, news, décisions Claude + justifications |
| `/trading`   | Auto ON/OFF, Paper/Live, validation opportunités IA, ordre manuel, fermeture, SL/TP |
| `/portfolio` | Valeur, historique, allocation, positions, historique des trades    |
| `/risk`      | Exposition globale/par actif, limites, positions protégées, alertes |
| `/settings`  | Session, matrice RBAC, connexions temps réel, préférences           |

## Scripts

```bash
npm run dev        # dev server
npm run build      # build production
npm run start      # serveur production
npm run typecheck  # tsc --noEmit
npm run lint       # eslint
```

## Sécurité

- Authentification **JWT** (bearer injecté par l'intercepteur axios ;
  `401 → cmi:unauthorized → logout`).
- **RBAC** centralisé (`src/lib/auth/rbac.ts`) : chaque action mutante est gated
  par une permission ; les contrôles interdits sont **désactivés** (pas cachés).
- Le token est transmis au WebSocket gateway en query `?token=`.
