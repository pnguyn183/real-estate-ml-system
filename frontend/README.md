# Real-estate prediction frontend

This directory contains the React 18 + TypeScript + Vite browser client served
by Nginx in the `frontend` Compose service (host port 3000). It calls the
authenticated FastAPI service through the same-origin `/api` proxy (Vite in
development and Nginx in Compose). An explicit `VITE_API_URL` overrides this at
build time.

## User-facing behavior

- Register and sign in with bearer-token authentication. The first account is
  promoted to `admin` by the backend.
- Submit a single property prediction and view the returned estimate,
  heuristic interval and explanations.
- `manager` and `admin` users can view model information. Batch prediction is
  available through the backend API, with no batch form in this client.
  `admin` users can manage roles and account status.
- The API, rather than the UI, is the authority for permissions.

## Local development

Prerequisites: Node.js 18+ and npm 8+.

```bash
npm install
npm run dev
```

Vite serves `http://localhost:3000` and proxies `/api` to `http://localhost:8000`.
For a separately hosted backend, set `VITE_API_URL` before building:

```bash
VITE_API_URL=http://localhost:8000
```

Production checks:

```bash
npm run type-check
npm run lint
npm run build
npm run preview
```

The Compose image uses `npm ci` and `npm run build`, then serves `dist/` with
Nginx. ESLint checks TypeScript and React hooks. There are no browser automation
tests in this checkout; a passing build/lint is not evidence of browser behavior.

## Source layout

```text
src/
├── api/client.ts
├── components/
│   ├── AuthPanel.tsx
│   ├── Header.tsx
│   ├── ModelInfo.tsx
│   ├── PredictionForm.tsx
│   ├── ResultsDisplay.tsx
│   ├── StatsCard.tsx
│   └── UserAdminPanel.tsx
├── App.tsx
├── main.tsx
└── index.css
```

## API calls

The client uses `src/api/client.ts` and stores the bearer token in browser
storage. It calls:

```text
GET  /health
POST /auth/register
POST /auth/login
GET  /auth/me
GET  /model/info                 (manager/admin)
POST /predict                    (user/manager/admin)
GET  /auth/users                 (admin)
PATCH /auth/users/{id}/role      (admin)
PATCH /auth/users/{id}/status    (admin)
```

The backend additionally exposes `POST /predict/batch` (manager/admin) and public
`GET /ready`. Readiness returns 503 when the model is missing or cannot load;
`GET /health` remains a liveness/status endpoint so accounts and the UI can work
before the first model is trained. Batch results include original `input_index`
values and indexed `failures`; request `include_confidence: true` to include
heuristic confidence fields.

For request fields and response shapes, use the Pydantic models in
`modeling/api.py`; [`../docs/DATA_SCHEMA.md`](../docs/DATA_SCHEMA.md) covers
the feature fields.

## Troubleshooting

- Check `curl http://localhost:8000/health` and confirm `VITE_API_URL`.
- CORS origins are configured by the backend environment; the browser URL must
  be allowed there.
- A 401 means the token is missing/expired. Register the first user or ask an
  admin to grant `manager` access.
- If the build fails, run `npm install` (or `npm ci` with the existing lockfile)
  and then `npm run type-check`/`npm run build`. Do not remove the lockfile as
  part of normal troubleshooting.

**Last reviewed:** 2026-09-25
