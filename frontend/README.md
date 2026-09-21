# Real-estate prediction frontend

This directory contains the React 18 + TypeScript + Vite browser client served
by Nginx in the `frontend` Compose service (host port 3000). It calls the
authenticated FastAPI service at port 8000.

## User-facing behavior

- Register and sign in with bearer-token authentication. The first account is
  promoted to `admin` by the backend.
- Submit a single property prediction and view the returned estimate,
  heuristic interval and explanations.
- `manager` and `admin` users can view model information; they can call batch
  prediction. `admin` users can manage roles and account status.
- The API, rather than the UI, is the authority for permissions.

## Local development

Prerequisites: Node.js 18+ and npm 8+.

```bash
npm install
npm run dev
```

Vite serves `http://localhost:3000`. Set `VITE_API_URL` when the API is not at
the default `http://localhost:8000`:

```bash
VITE_API_URL=http://localhost:8000
```

Production checks:

```bash
npm run type-check
npm run build
npm run preview
```

The Compose image uses `npm ci` and `npm run build`, then serves `dist/` with
Nginx. There are no frontend test files in this checkout. The `lint` script is
declared but currently has no ESLint configuration, so it is not a passing
validation command yet.

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
POST /predict/batch              (manager/admin)
GET  /auth/users                 (admin)
PATCH /auth/users/{id}/role      (admin)
PATCH /auth/users/{id}/status    (admin)
```

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

**Last reviewed:** 2026-08-27
