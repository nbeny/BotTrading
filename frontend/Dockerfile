# ── CMI Web Terminal — production image ────────────────────────────────────
FROM node:22-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json* ./
RUN npm ci

FROM node:22-alpine AS builder
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .
# NEXT_PUBLIC_* are inlined into the client bundle at build time.
ARG NEXT_PUBLIC_USE_MOCK=1
ARG NEXT_PUBLIC_WS_URL=ws://localhost:8080/ws
ARG NEXT_PUBLIC_API_BASE=/api/gateway
ARG NEXT_PUBLIC_CONTROL_BASE=/api/control
# Cloudflare Turnstile site key (public half; empty = captcha off on the form).
ARG NEXT_PUBLIC_TURNSTILE_SITE_KEY=
ENV NEXT_PUBLIC_USE_MOCK=$NEXT_PUBLIC_USE_MOCK
ENV NEXT_PUBLIC_WS_URL=$NEXT_PUBLIC_WS_URL
ENV NEXT_PUBLIC_API_BASE=$NEXT_PUBLIC_API_BASE
ENV NEXT_PUBLIC_CONTROL_BASE=$NEXT_PUBLIC_CONTROL_BASE
ENV NEXT_PUBLIC_TURNSTILE_SITE_KEY=$NEXT_PUBLIC_TURNSTILE_SITE_KEY
# next.config.mjs bakes the rewrite() destinations into the routes manifest at
# BUILD time, so the api-gateway / control-api targets must be provided here —
# a runtime env is ignored for rewrites. Point them at the compose service names.
ARG API_GATEWAY_URL=http://localhost:8000
ARG CONTROL_API_URL=http://localhost:8001
ENV API_GATEWAY_URL=$API_GATEWAY_URL
ENV CONTROL_API_URL=$CONTROL_API_URL
ENV NEXT_TELEMETRY_DISABLED=1
RUN npm run build

FROM node:22-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production
ENV NEXT_TELEMETRY_DISABLED=1
RUN addgroup -g 1001 -S nodejs && adduser -S nextjs -u 1001
COPY --from=builder /app/public ./public
COPY --from=builder /app/.next ./.next
COPY --from=builder /app/node_modules ./node_modules
COPY --from=builder /app/package.json ./package.json
USER nextjs
EXPOSE 3000
CMD ["npm", "run", "start"]
