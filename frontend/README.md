# Real Estate Price Predictor - Frontend

A React + TypeScript frontend for the Real Estate Price Prediction system.

## Features

**Prediction workspace**
- Single property prediction form
- Real-time prediction results
- Role-aware model information dashboard

**Authentication and access control**
- Register and sign in with bearer-token authentication
- The first registered account becomes `admin`
- Admin panel for user role and account status management

**Technical**
- React 18 and TypeScript
- Vite for development and production builds
- Axios API client with bearer-token injection
- Tailwind CSS and Lucide icons

## Getting Started

### Prerequisites

- Node.js 18+ and npm 8+
- Backend API running on http://localhost:8000

### Installation

```bash
npm install

# Create .env if needed
# VITE_API_URL=http://localhost:8000
```

### Development

```bash
npm run dev
```

The default dev URL is http://localhost:3000.

### Building

```bash
npm run build
npm run preview
```

## Project Structure

```text
frontend/
├── src/
│   ├── api/client.ts
│   ├── components/
│   │   ├── AuthPanel.tsx
│   │   ├── Header.tsx
│   │   ├── ModelInfo.tsx
│   │   ├── PredictionForm.tsx
│   │   ├── ResultsDisplay.tsx
│   │   ├── StatsCard.tsx
│   │   └── UserAdminPanel.tsx
│   ├── App.tsx
│   ├── main.tsx
│   └── index.css
├── index.html
├── package.json
├── tailwind.config.js
├── tsconfig.json
└── vite.config.ts
```

## API Integration

The frontend communicates with the backend API at `VITE_API_URL`, defaulting to `http://localhost:8000`.

Available endpoints:

- `GET /health` - Public API health check
- `POST /auth/register` - Create an account; first account is `admin`
- `POST /auth/login` - Sign in and receive a bearer token
- `GET /auth/me` - Restore the current authenticated session
- `GET /model/info` - Model information for `manager` and `admin`
- `POST /predict` - Single prediction for `user`, `manager`, and `admin`
- `POST /predict/batch` - Batch prediction for `manager` and `admin`
- `GET /auth/users`, `PATCH /auth/users/{id}/role`, `PATCH /auth/users/{id}/status` - Admin-only access control

See `src/api/client.ts` for request helpers and token storage.

## Role Behavior

- `user`: can submit single predictions.
- `manager`: can submit single predictions, view model info, and call batch prediction APIs.
- `admin`: can do everything and manage accounts.

The UI hides unavailable controls, but permissions are enforced by the API.

## Environment Variables

```bash
VITE_API_URL=http://localhost:8000
```

## Troubleshooting

### API Connection Issues

1. Check the backend: `curl http://localhost:8000/health`
2. Verify `VITE_API_URL` in `.env`
3. Check backend CORS origins include the frontend URL

### Sign-in or Permission Issues

1. Register the first account to bootstrap `admin`
2. Use a password with at least 8 characters, one uppercase letter, one lowercase letter, and one number
3. Ask an admin to promote accounts that need `manager` access
4. Sign in again if the API returns `401`

### Build Issues

```bash
rm -rf node_modules package-lock.json
npm install
npm run build
```

`npm run lint` currently requires an ESLint config file before it can run.

## License

Same as parent project.
