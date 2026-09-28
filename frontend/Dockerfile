FROM node:20.18.1-alpine AS deps
WORKDIR /app
COPY package.json package-lock.json ./
RUN npm ci
FROM node:20.18.1-alpine AS builder
WORKDIR /app
COPY --from=deps /app/node_modules ./node_modules
COPY . .
ENV NEXT_TELEMETRY_DISABLED=1
RUN npm run build
FROM node:20.18.1-alpine AS runtime
ENV NODE_ENV=production NEXT_TELEMETRY_DISABLED=1 PORT=3000 HOSTNAME=0.0.0.0
WORKDIR /app
RUN addgroup -S -g 10001 medflow && adduser -S -u 10001 -G medflow medflow
COPY --from=builder --chown=medflow:medflow /app/.next/standalone ./
COPY --from=builder --chown=medflow:medflow /app/.next/static ./.next/static
COPY --from=builder --chown=medflow:medflow /app/public ./public
USER 10001:10001
EXPOSE 3000
CMD ["node", "server.js"]
