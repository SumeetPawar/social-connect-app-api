# Azure App Service Deployment Guide (Code-Based)

## Files Prepared for Deployment

Your FastAPI app is now ready for code-based deployment to Azure App Service.

### ✅ Kept Files
- `app/` — FastAPI source code
- `requirements.txt` — Python dependencies
- `alembic/` — Database migrations
- `alembic.ini` — Migration config
- `.env` — Local environment (won't be deployed)

### ❌ Remove (Optional)
- `Dockerfile` — Not needed for code deployment
- `docker-compose.yml` — Not needed for code deployment
- `docker-compose.prod.yml` — Not needed for code deployment
- `nginx.conf` — App Service handles web server
- `fitness-tracker.service` — Not used on App Service
- `DEPLOYMENT.md` — Outdated
- `test_reminders.py` — Optional test file

## Changes Made

1. **app/main.py**
   - Updated CORS origins to use `settings.CORS_ORIGINS` (from environment)
   - Added PORT environment variable handling for Azure App Service
   - Added if `__name__ == "__main__"` block for local testing

2. **app/core/config.py**
   - Added `VAPID_PUBLIC_KEY` and `VAPID_PRIVATE_KEY` to Settings

3. **alembic/env.py**
   - Already configured to read `DATABASE_URL` from environment ✓

4. **requirements.txt**
   - Already includes `gunicorn` and all required dependencies ✓

## Environment Variables Required in Azure App Service

Set these in App Service > Configuration > Application settings:

```
DATABASE_URL = postgresql+asyncpg://gesadmin@ges-social-pg-prod:YOUR_PASSWORD@ges-social-pg-prod.postgres.database.azure.com/fitness_tracker?ssl=require

CORS_ORIGINS = ["https://your-frontend-domain.com", "http://localhost:3000"]

JWT_SECRET = your-secure-jwt-secret-key

VAPID_PUBLIC_KEY = your-vapid-public-key

VAPID_PRIVATE_KEY = your-vapid-private-key
```

## Deployment Steps

### Option 1: GitHub Actions (Recommended)
1. Push your code to GitHub
2. In App Service > Deployment Center > Source: GitHub
3. Authorize and select your repo/branch
4. Azure automatically builds and deploys on push

### Option 2: Local Git
1. In App Service > Deployment Center > Source: Local Git
2. Get the Git remote URL
3. Run:
   ```bash
   git remote add azure <URL>
   git push azure main
   ```

### Option 3: Zip Deploy
1. Create a zip file with `app/`, `alembic/`, `.env`, `requirements.txt`, `alembic.ini`
2. In Azure Portal, go to App Service > Deployment Center > Manual Deployment
3. Upload the zip file

## Post-Deployment

1. Set all environment variables in App Service Configuration
2. Restart the app
3. Check "Log stream" for any errors
4. App Service will:
   - Install dependencies from requirements.txt
   - Run migrations via startup command (if configured)
   - Start the app with Gunicorn

## Startup Command (Optional)

If App Service doesn't detect the startup correctly, set this in Configuration > Startup Command:

```
gunicorn -w 4 -k uvicorn.workers.UvicornWorker app.main:app
```

## Networking

For private database access:
1. Set up VNet integration in App Service > Networking > VNet Integration
2. Use the private hostname in DATABASE_URL
3. Ensure database firewall allows the App Service subnet

## Health Check (Optional)

Enable in App Service > Health check:
- Path: `/health`
- This helps Azure restart unhealthy instances

---

Ready to deploy! Let me know if you need help with any specific deployment method.
