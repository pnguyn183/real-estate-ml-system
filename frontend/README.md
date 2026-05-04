# Real Estate Price Predictor - Frontend

A modern, professional React frontend for the Real Estate Price Prediction system.

## Features

✨ **Beautiful UI**
- Modern design with Tailwind CSS
- Responsive layout (mobile, tablet, desktop)
- Glass-morphism effects
- Smooth animations and transitions

📊 **Prediction Interface**
- Single property prediction form
- Batch prediction support
- Real-time results display
- Model information dashboard

🔧 **Technical**
- Built with React 18 & TypeScript
- Vite for fast development & building
- Axios for API communication
- Recharts for data visualization (ready for extension)

## Getting Started

### Prerequisites
- Node.js 16+ or npm 8+
- Backend API running on http://localhost:8000

### Installation

```bash
# Install dependencies
npm install

# Create .env file (copy from .env.example)
cp .env.example .env

# Update API URL if needed
# VITE_API_URL=http://localhost:8000
```

### Development

```bash
# Start dev server (runs on http://localhost:3000)
npm run dev
```

### Building

```bash
# Build for production
npm run build

# Preview production build
npm run preview
```

## Project Structure

```
frontend/
├── public/              # Static assets
├── src/
│   ├── api/            # API client functions
│   ├── components/     # React components
│   │   ├── Header.tsx
│   │   ├── PredictionForm.tsx
│   │   ├── ResultsDisplay.tsx
│   │   ├── StatsCard.tsx
│   │   └── ModelInfo.tsx
│   ├── App.tsx         # Main app component
│   ├── main.tsx        # Entry point
│   └── index.css       # Tailwind styles
├── index.html          # HTML template
├── vite.config.ts      # Vite configuration
├── tsconfig.json       # TypeScript configuration
├── tailwind.config.js  # Tailwind CSS configuration
└── package.json        # Dependencies
```

## API Integration

The frontend communicates with the backend API at `http://localhost:8000`. 

### Available Endpoints

- `GET /health` - Check API health
- `GET /model/info` - Get model information
- `POST /predict` - Predict for single property
- `POST /predict/batch` - Predict for multiple properties

See `/src/api/client.ts` for implementation details.

## Styling

This project uses **Tailwind CSS** for styling. Custom configurations can be found in `tailwind.config.js`.

### Color Scheme
- Primary: Blue/Cyan gradient
- Accent: Various gradient colors
- Background: Gradient backgrounds with glassmorphism effects

## Deployment

### Docker

```dockerfile
FROM node:18-alpine as builder
WORKDIR /app
COPY package*.json ./
RUN npm install
COPY . .
RUN npm run build

FROM nginx:alpine
COPY --from=builder /app/dist /usr/share/nginx/html
EXPOSE 80
CMD ["nginx", "-g", "daemon off;"]
```

### Environment Variables

Create a `.env` file based on `.env.example`:

```
VITE_API_URL=http://your-api-host:8000
```

## Browser Support

- Chrome (latest)
- Firefox (latest)
- Safari (latest)
- Edge (latest)

## Performance

- ⚡ Fast development with Vite
- 📦 Optimized production builds
- 🎯 Tree-shaking for minimal bundle size
- 💨 CSS purging with Tailwind

## Troubleshooting

### API Connection Issues

If you see "Failed to fetch model info" error:

1. Check if backend is running: `curl http://localhost:8000/health`
2. Check CORS settings in backend
3. Verify `VITE_API_URL` in `.env`

### Build Issues

```bash
# Clear cache and reinstall
rm -rf node_modules package-lock.json
npm install
npm run build
```

## License

Same as parent project

## Contributing

1. Create a feature branch
2. Make your changes
3. Submit a pull request

## Support

For issues or questions, please open an issue in the main repository.
