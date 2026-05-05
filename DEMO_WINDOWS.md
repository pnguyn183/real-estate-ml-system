# Windows Demo Quick Start

For Windows users, here's the fastest way to run the demo:

## Prerequisites

- Docker Desktop installed and running
- Git Bash or PowerShell

## Quick Start

### Using PowerShell

```powershell
# Navigate to project directory
cd real-estate-ml-system

# Run demo
docker-compose -f docker-compose.demo.yml up
```

### Using Git Bash

```bash
chmod +x scripts/demo_run.sh
bash scripts/demo_run.sh
```

### Using WSL (Windows Subsystem for Linux)

```bash
wsl
cd /mnt/c/path/to/real-estate-ml-system
bash scripts/demo_run.sh
```

## Accessing Services

Open in your browser:

- **Frontend**: http://localhost:3000
- **API**: http://localhost:8000
- **Grafana**: http://localhost:3001 (admin/admin)
- **MongoDB UI**: http://localhost:8081

## Checking Progress

In PowerShell:

```powershell
# See all containers
docker-compose -f docker-compose.demo.yml ps

# Follow scraper
docker-compose -f docker-compose.demo.yml logs -f scraper

# Follow trainer
docker-compose -f docker-compose.demo.yml logs -f trainer
```

## Stop Demo

```powershell
docker-compose -f docker-compose.demo.yml down
```

## Troubleshooting

**Containers keep restarting?**
```powershell
# Check logs
docker-compose -f docker-compose.demo.yml logs
```

**Port already in use?**
```powershell
# Find what's using port 3000
netstat -ano | findstr :3000

# Kill process (replace PID)
taskkill /PID <PID> /F
```

**Out of memory?**
- Docker Desktop → Settings → Resources → Increase Memory to 4GB+

For more help, see [DEMO.md](DEMO.md)
