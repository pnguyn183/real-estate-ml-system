# Build stage
FROM node:18-alpine as builder

WORKDIR /app

# Copy package files
COPY package*.json ./
COPY tsconfig*.json ./
COPY vite.config.ts ./
COPY postcss.config.js tailwind.config.js ./
COPY index.html ./

# Copy source
COPY src ./src

# Install dependencies
RUN npm ci

# Build
RUN npm run build

# Runtime stage
FROM nginx:alpine

# Copy nginx config
RUN echo 'server { \
  listen 80; \
  server_name _; \
  root /usr/share/nginx/html; \
  index index.html; \
  location / { \
    try_files $uri $uri/ /index.html; \
  } \
  location /api/ { \
    proxy_pass http://api:8000/; \
  } \
}' > /etc/nginx/conf.d/default.conf

# Copy built app from builder
COPY --from=builder /app/dist /usr/share/nginx/html

EXPOSE 80

HEALTHCHECK --interval=30s --timeout=10s --start-period=5s --retries=3 \
    CMD wget --quiet --tries=1 --spider http://localhost/ || exit 1

CMD ["nginx", "-g", "daemon off;"]
