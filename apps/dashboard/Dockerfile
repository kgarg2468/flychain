# syntax=docker/dockerfile:1.7
FROM node:20-alpine AS base
RUN corepack enable

FROM base AS deps
WORKDIR /app
COPY package.json pnpm-workspace.yaml pnpm-lock.yaml* .npmrc* ./
COPY apps/dashboard/package.json ./apps/dashboard/package.json
COPY apps/cli/package.json ./apps/cli/package.json
COPY packages/sdk-ts/package.json ./packages/sdk-ts/package.json
RUN pnpm install --frozen-lockfile=false

FROM base AS builder
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY --from=deps /app/apps/dashboard/node_modules ./apps/dashboard/node_modules
COPY . .
RUN pnpm -F @flychain/dashboard build

FROM node:20-alpine AS runner
WORKDIR /app
ENV NODE_ENV=production \
    PORT=3000 \
    HOSTNAME=0.0.0.0
RUN addgroup --system --gid 1001 nodejs \
 && adduser --system --uid 1001 nextjs
COPY --from=builder --chown=nextjs:nodejs /app/apps/dashboard/.next/standalone ./
COPY --from=builder --chown=nextjs:nodejs /app/apps/dashboard/.next/static ./apps/dashboard/.next/static
COPY --from=builder --chown=nextjs:nodejs /app/apps/dashboard/public ./apps/dashboard/public
USER nextjs
EXPOSE 3000
CMD ["node", "apps/dashboard/server.js"]
