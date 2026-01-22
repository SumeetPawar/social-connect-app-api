# Azure Deployment Guide - Simple Steps

## Prerequisites
- Azure account (free tier works)
- Azure CLI installed: https://aka.ms/installazurecli
- Docker installed (for building image)

---

## Option 1: Azure Container Apps (Recommended - Easiest)

## Quick Notes (Read Once)
- Use **Azure Container Apps** for the API (FastAPI).
- Use **Azure Database for PostgreSQL (Flexible Server)** for the DB.
- For your hourly reminders (APScheduler), keep the app **always running**: set `--min-replicas 1`.
- Your app URL will be created automatically by Azure Container Apps as a public hostname:
  `https://<app-name>.<random>.<region>.azurecontainerapps.io`

---

## Step-by-step (Windows PowerShell)

### Step 0: Login
```powershell
az login
```

### Step 1: Set Variables
```powershell
$RESOURCE_GROUP = "fitness-tracker-rg"
$LOCATION = "eastus"
$CONTAINER_APP_ENV = "fitness-env"
$CONTAINER_APP = "fitness-tracker-app"
$ACR_NAME = "fitnesstrackeracr123"   # must be globally unique, lowercase, no dashes
$DB_SERVER = "fitness-tracker-db"    # postgres server name
$DB_NAME = "fitness_tracker"         # database name
$DB_ADMIN = "fitadmin"
$DB_PASSWORD = "ChangeThis_ToAStrongPassword!"
```

### Step 2: Create Resource Group
```powershell
az group create --name $RESOURCE_GROUP --location $LOCATION
```

### Step 3: Create PostgreSQL Flexible Server + Database
```powershell
az postgres flexible-server create `
  --resource-group $RESOURCE_GROUP `
  --name $DB_SERVER `
  --location $LOCATION `
  --admin-user $DB_ADMIN `
  --admin-password $DB_PASSWORD `
  --sku-name Standard_B1ms `
  --tier Burstable `
  --storage-size 32 `
  --version 15 `
  --public-access 0.0.0.0

az postgres flexible-server db create `
  --resource-group $RESOURCE_GROUP `
  --server-name $DB_SERVER `
  --database-name $DB_NAME
```

### Step 4: Create Azure Container Registry (ACR)
```powershell
az acr create `
  --resource-group $RESOURCE_GROUP `
  --name $ACR_NAME `
  --sku Basic `
  --admin-enabled true
```

### Step 5: Build + Push Docker Image
```powershell
az acr login --name $ACR_NAME

docker build -t $ACR_NAME.azurecr.io/fitness-tracker:latest .
docker push $ACR_NAME.azurecr.io/fitness-tracker:latest
```

### Step 6: Get ACR Credentials
```powershell
$ACR_USERNAME = az acr credential show --name $ACR_NAME --query username -o tsv
$ACR_PASSWORD = az acr credential show --name $ACR_NAME --query "passwords[0].value" -o tsv
```

### Step 7: Create Container Apps Environment
```powershell
az containerapp env create `
  --name $CONTAINER_APP_ENV `
  --resource-group $RESOURCE_GROUP `
  --location $LOCATION
```

### Step 8: Generate JWT Secret
```powershell
$JWT_SECRET = python -c "import secrets; print(secrets.token_urlsafe(32))"
```

### Step 9: Build DATABASE_URL (with SSL)
```powershell
$DB_HOST = az postgres flexible-server show --resource-group $RESOURCE_GROUP --name $DB_SERVER --query fullyQualifiedDomainName -o tsv
$DATABASE_URL = "postgresql+asyncpg://$DB_ADMIN:$DB_PASSWORD@$DB_HOST/$DB_NAME?ssl=require"
```

### Step 10: Deploy Container App (Always-on: min-replicas=1)
```powershell
az containerapp create `
  --name $CONTAINER_APP `
  --resource-group $RESOURCE_GROUP `
  --environment $CONTAINER_APP_ENV `
  --image $ACR_NAME.azurecr.io/fitness-tracker:latest `
  --target-port 8000 `
  --ingress external `
  --registry-server $ACR_NAME.azurecr.io `
  --registry-username $ACR_USERNAME `
  --registry-password $ACR_PASSWORD `
  --cpu 0.5 --memory 1.0Gi `
  --min-replicas 1 --max-replicas 3 `
  --env-vars `
    DATABASE_URL=$DATABASE_URL `
    JWT_SECRET=$JWT_SECRET `
    ENV=production `
    CORS_ORIGINS='["https://your-frontend.com"]'
```

### Step 11: Get Your App URL
```powershell
$APP_URL = az containerapp show `
  --name $CONTAINER_APP `
  --resource-group $RESOURCE_GROUP `
  --query properties.configuration.ingress.fqdn -o tsv

"https://$APP_URL"
```

### Step 12: View Logs
```powershell
az containerapp logs show `
  --name $CONTAINER_APP `
  --resource-group $RESOURCE_GROUP `
  --follow
```

---

## Step-by-step (Bash/Linux/macOS)

### Step 1: Login to Azure
```bash
az login
```

### Step 2: Set Variables
```bash
RESOURCE_GROUP="fitness-tracker-rg"
LOCATION="eastus"
CONTAINER_APP_ENV="fitness-env"
CONTAINER_APP="fitness-tracker-app"
ACR_NAME="fitnesstrackeracr123"  # Must be globally unique, lowercase, no dashes
DB_NAME="fitness-tracker-db"
```

### Step 3: Create Resource Group
```bash
az group create --name $RESOURCE_GROUP --location $LOCATION
```

### Step 4: Create PostgreSQL Database
```bash
# Create PostgreSQL server
az postgres flexible-server create \
  --resource-group $RESOURCE_GROUP \
  --name $DB_NAME \
  --location $LOCATION \
  --admin-user fitadmin \
  --admin-password "YourSecurePassword123!" \
  --sku-name Standard_B1ms \
  --tier Burstable \
  --storage-size 32 \
  --version 15 \
  --public-access 0.0.0.0

# Create database
az postgres flexible-server db create \
  --resource-group $RESOURCE_GROUP \
  --server-name $DB_NAME \
  --database-name fitness_tracker
```

### Step 5: Create Container Registry
```bash
az acr create \
  --resource-group $RESOURCE_GROUP \
  --name $ACR_NAME \
  --sku Basic \
  --admin-enabled true
```

### Step 6: Build and Push Docker Image
```bash
# Login to ACR
az acr login --name $ACR_NAME

# Build and push
docker build -t $ACR_NAME.azurecr.io/fitness-tracker:latest .
docker push $ACR_NAME.azurecr.io/fitness-tracker:latest
```

### Step 7: Get ACR Credentials
```bash
ACR_USERNAME=$(az acr credential show --name $ACR_NAME --query username -o tsv)
ACR_PASSWORD=$(az acr credential show --name $ACR_NAME --query "passwords[0].value" -o tsv)
```

### Step 8: Create Container Apps Environment
```bash
az containerapp env create \
  --name $CONTAINER_APP_ENV \
  --resource-group $RESOURCE_GROUP \
  --location $LOCATION
```

### Step 9: Generate JWT Secret
```bash
JWT_SECRET=$(python -c "import secrets; print(secrets.token_urlsafe(32))")
```

### Step 10: Deploy Container App
```bash
# Get database connection string
DB_HOST=$(az postgres flexible-server show --resource-group $RESOURCE_GROUP --name $DB_NAME --query fullyQualifiedDomainName -o tsv)
DATABASE_URL="postgresql+asyncpg://fitadmin:YourSecurePassword123!@$DB_HOST/fitness_tracker?ssl=require"

# Deploy the app
az containerapp create \
  --name $CONTAINER_APP \
  --resource-group $RESOURCE_GROUP \
  --environment $CONTAINER_APP_ENV \
  --image $ACR_NAME.azurecr.io/fitness-tracker:latest \
  --target-port 8000 \
  --ingress external \
  --registry-server $ACR_NAME.azurecr.io \
  --registry-username $ACR_USERNAME \
  --registry-password $ACR_PASSWORD \
  --cpu 0.5 --memory 1.0Gi \
  --min-replicas 1 --max-replicas 3 \
  --env-vars \
    DATABASE_URL="$DATABASE_URL" \
    JWT_SECRET="$JWT_SECRET" \
    ENV=production \
    CORS_ORIGINS='["https://your-frontend.com"]'
```

### Step 11: Get Your App URL
```bash
az containerapp show \
  --name $CONTAINER_APP \
  --resource-group $RESOURCE_GROUP \
  --query properties.configuration.ingress.fqdn -o tsv
```

Your app is now live at: `https://your-app-url.azurecontainerapps.io`

### Step 12: View Logs
```bash
az containerapp logs show \
  --name $CONTAINER_APP \
  --resource-group $RESOURCE_GROUP \
  --follow
```

---

## Option 2: Azure App Service (Alternative)

### Step 1-4: Same as above (Login, Variables, Resource Group, Database)

### Step 5: Create App Service Plan
```bash
az appservice plan create \
  --name fitness-plan \
  --resource-group $RESOURCE_GROUP \
  --location $LOCATION \
  --is-linux \
  --sku B1
```

### Step 6: Create Web App
```bash
az webapp create \
  --resource-group $RESOURCE_GROUP \
  --plan fitness-plan \
  --name $CONTAINER_APP \
  --deployment-container-image-name $ACR_NAME.azurecr.io/fitness-tracker:latest
```

### Step 7: Configure Web App
```bash
# Get DB connection
DB_HOST=$(az postgres flexible-server show --resource-group $RESOURCE_GROUP --name $DB_NAME --query fullyQualifiedDomainName -o tsv)
DATABASE_URL="postgresql+asyncpg://fitadmin:YourSecurePassword123!@$DB_HOST/fitness_tracker?ssl=require"
JWT_SECRET=$(python -c "import secrets; print(secrets.token_urlsafe(32))")

# Set environment variables
az webapp config appsettings set \
  --resource-group $RESOURCE_GROUP \
  --name $CONTAINER_APP \
  --settings \
    DATABASE_URL="$DATABASE_URL" \
    JWT_SECRET="$JWT_SECRET" \
    ENV=production \
    WEBSITES_PORT=8000

# Enable container logging
az webapp log config \
  --name $CONTAINER_APP \
  --resource-group $RESOURCE_GROUP \
  --docker-container-logging filesystem
```

### Step 8: Get Your App URL
```bash
echo "https://$CONTAINER_APP.azurewebsites.net"
```

---

## Update Deployment (After Code Changes)

### For Container Apps:
```bash
# Rebuild and push
docker build -t $ACR_NAME.azurecr.io/fitness-tracker:latest .
docker push $ACR_NAME.azurecr.io/fitness-tracker:latest

# Update container app
az containerapp update \
  --name $CONTAINER_APP \
  --resource-group $RESOURCE_GROUP \
  --image $ACR_NAME.azurecr.io/fitness-tracker:latest
```

### For App Service:
```bash
# Rebuild and push
docker build -t $ACR_NAME.azurecr.io/fitness-tracker:latest .
docker push $ACR_NAME.azurecr.io/fitness-tracker:latest

# Restart to pull new image
az webapp restart --name $CONTAINER_APP --resource-group $RESOURCE_GROUP
```

---

## Important: Update VAPID Keys

After deployment, update [app/services/push_notify.py](app/services/push_notify.py) to read VAPID keys from environment variables:

```python
import os

VAPID_PRIVATE_KEY = os.getenv("VAPID_PRIVATE_KEY", "VbZrKrsZGzTkKGfPGHhMDVGZ_7ZkwICWmReBxAEywb0")
VAPID_PUBLIC_KEY = os.getenv("VAPID_PUBLIC_KEY", "BMmVTo0GaTfa9QJSmxlmXrE3ukC6wfZKBRgxxkjBBpvEfBK8-9iNOSGxH04kZPaKCuRccatRgPGlrxnGDIr0O0Y")
VAPID_CLAIMS = {
    "sub": os.getenv("VAPID_EMAIL", "mailto:admin@example.com")
}
```

Then add to deployment:
```bash
--env-vars VAPID_PRIVATE_KEY="your-key" VAPID_PUBLIC_KEY="your-key" VAPID_EMAIL="mailto:your-email"
```

---

## Cost Breakdown & Optimization

### Standard Deployment (Container Apps)
| Service | Configuration | Cost/Month |
|---------|--------------|------------|
| Container Apps | 0.5 vCPU, 1GB RAM, always-on | ~$13 |
| PostgreSQL Flexible | Burstable B1ms (1 vCore, 2GB) | ~$13 |
| Container Registry | Basic (10GB storage) | ~$5 |
| Bandwidth | First 100GB free | $0 |
| **TOTAL** | | **~$31/month** |

---

## 💰 Cost Reduction Strategies

### Option 1: Ultra-Low Cost (~$5-8/month)
**For hobby projects or testing**

#### Use Consumption-Based Container Apps
```bash
az containerapp create \
  --name $CONTAINER_APP \
  --resource-group $RESOURCE_GROUP \
  --environment $CONTAINER_APP_ENV \
  --image $ACR_NAME.azurecr.io/fitness-tracker:latest \
  --target-port 8000 \
  --ingress external \
  --registry-server $ACR_NAME.azurecr.io \
  --registry-username $ACR_USERNAME \
  --registry-password $ACR_PASSWORD \
  --cpu 0.25 --memory 0.5Gi \
  # Scale to zero when not used
  --min-replicas 0 --max-replicas 1 \
  --scale-rule-name http-rule \
  --scale-rule-type http \
  --scale-rule-http-concurrency 10
```

#### Use Smaller PostgreSQL
```bash
az postgres flexible-server create \
  --resource-group $RESOURCE_GROUP \
  --name $DB_NAME \
  --location $LOCATION \
  --admin-user fitadmin \
  --admin-password "YourSecurePassword123!" \
  --sku-name Standard_B1ms \
  --tier Burstable \
  # Minimum size
  --storage-size 32 \
  --version 15 \
  --public-access 0.0.0.0 \
  # No HA
  --high-availability Disabled \
  --backup-retention 7  # Minimum backup retention
```

**Savings:**
- Container Apps (scale to zero): ~$3/month (only when active)
- PostgreSQL B1ms: ~$8/month (stop when not needed)
- Container Registry Basic: ~$5/month
- **Total: ~$8-16/month** (depending on usage)

---

### Option 2: Free/Near-Free (~$0-5/month)
**For development or very light production**

#### 1. Use Azure Free Services
```bash
# Option A: Deploy to Azure Container Instances (pay per second)
az container create \
  --resource-group $RESOURCE_GROUP \
  --name $CONTAINER_APP \
  --image $ACR_NAME.azurecr.io/fitness-tracker:latest \
  --cpu 0.5 --memory 0.5 \
  --registry-login-server $ACR_NAME.azurecr.io \
  --registry-username $ACR_USERNAME \
  --registry-password $ACR_PASSWORD \
  --dns-name-label fitness-tracker-unique \
  --ports 8000 \
  --environment-variables \
    DATABASE_URL="$DATABASE_URL" \
    JWT_SECRET="$JWT_SECRET"
```

#### 2. Use Free PostgreSQL Alternative
**Option A: Supabase (Generous free tier)**
- Go to supabase.com
- Create free PostgreSQL database
- Get connection string
- **Cost: $0/month** (500MB database, 2GB bandwidth)

**Option B: Azure Cosmos DB for PostgreSQL (Free tier)**
```bash
# 32GB storage, 400 RU/s free forever
az cosmosdb create \
  --name fitness-cosmos \
  --resource-group $RESOURCE_GROUP \
  --kind GlobalDocumentDB \
  --enable-free-tier true
```

**Option C: Railway.app (Free tier)**
- 500 hours/month free
- PostgreSQL included
- **Cost: $0/month** (for light usage)

#### 3. Use GitHub Container Registry (Free)
Instead of Azure Container Registry:
```bash
# Build and push to GitHub
echo $GITHUB_TOKEN | docker login ghcr.io -u USERNAME --password-stdin
docker build -t ghcr.io/username/fitness-tracker:latest .
docker push ghcr.io/username/fitness-tracker:latest
```
**Savings: $5/month**

**Total Free Option: $0-5/month**

---

### Option 3: Serverless Alternative (~$2-5/month)
**Use Azure Functions + PostgreSQL**

Convert to Azure Functions:
- API endpoints as HTTP triggers
- Timer trigger for scheduler (instead of APScheduler)
- PostgreSQL on free external service

**Benefits:**
- Pay only per execution
- 1 million executions free/month
- **Cost: ~$2-5/month** (for moderate usage)

---

### Option 4: Stop Database When Not Needed
**Manual cost control**

```bash
# Stop PostgreSQL (saves ~$13/month when stopped)
az postgres flexible-server stop \
  --resource-group $RESOURCE_GROUP \
  --name $DB_NAME

# Start when needed
az postgres flexible-server start \
  --resource-group $RESOURCE_GROUP \
  --name $DB_NAME
```

**Savings:** ~$13/month when database is stopped

---

## 💡 Best Practices for Cost Optimization

### 1. Set Spending Limits
```bash
# Create budget alert
az consumption budget create \
  --budget-name fitness-tracker-budget \
  --amount 20 \
  --time-grain Monthly \
  --resource-group $RESOURCE_GROUP
```

### 2. Enable Auto-Stop for Dev Environment
```bash
# Scale Container App to zero during night (11 PM - 7 AM)
az containerapp update \
  --name $CONTAINER_APP \
  --resource-group $RESOURCE_GROUP \
  --min-replicas 0
```

### 3. Use Spot Instances (70% cheaper)
Not available for Container Apps, but for VMs:
```bash
# If using VM-based deployment
az vm create --priority Spot --max-price -1
```

### 4. Monitor and Optimize
```bash
# Check your current costs
az consumption usage list \
  --start-date 2026-01-01 \
  --end-date 2026-01-31 \
  --query "[?resourceGroup=='$RESOURCE_GROUP'].{Name:instanceName, Cost:pretaxCost}" -o table
```

### 5. Use Reserved Instances (40% savings)
For 1-year or 3-year commitment on PostgreSQL:
```bash
# Purchase reservation through Azure Portal
# Reservations > Add > Azure Database for PostgreSQL
# Savings: ~40% for 1-year, ~60% for 3-year
```

---

## 📊 Recommended Configurations by Use Case

### Personal Project / Testing
- **Container Apps**: 0.25 vCPU, 0.5GB RAM, min-replicas: 0
- **PostgreSQL**: B1ms with auto-stop script
- **Container Registry**: Use GitHub Container Registry (free)
- **Cost: ~$5-8/month**

### Small Production App (<1000 users)
- **Container Apps**: 0.5 vCPU, 1GB RAM, min-replicas: 1
- **PostgreSQL**: B1ms (Burstable)
- **Container Registry**: Basic
- **Cost: ~$25-31/month**

### Medium Production App (1000-10000 users)
- **Container Apps**: 1 vCPU, 2GB RAM, min-replicas: 2, max: 5
- **PostgreSQL**: Standard_D2s_v3 (2 vCore, 8GB)
- **Container Registry**: Standard
- **Cost: ~$150-200/month**

---

## 🎯 My Recommendation for You

**Start with Ultra-Low Cost Setup:**

1. **Use Supabase for free PostgreSQL** ($0/month)
2. **GitHub Container Registry** ($0/month)
3. **Azure Container Apps with scale-to-zero** (~$3-8/month)

**Total: ~$3-8/month** 

Once you have users and revenue, upgrade to Standard deployment (~$31/month).

---

## Troubleshooting

### View Container Logs:
```bash
az containerapp logs show --name $CONTAINER_APP --resource-group $RESOURCE_GROUP --follow
```

### Test Database Connection:
```bash
az postgres flexible-server connect --name $DB_NAME --admin-user fitadmin
```

### Check App Status:
```bash
az containerapp show --name $CONTAINER_APP --resource-group $RESOURCE_GROUP
```

### Restart App:
```bash
az containerapp revision restart --name $CONTAINER_APP --resource-group $RESOURCE_GROUP
```

---

## Clean Up Resources (Delete Everything)

```bash
az group delete --name $RESOURCE_GROUP --yes --no-wait
```

This removes all resources and stops billing.

---

## Next Steps

1. ✅ Update CORS_ORIGINS with your frontend URL
2. ✅ Move VAPID keys to environment variables
3. ✅ Setup custom domain (optional)
4. ✅ Configure Azure Monitor for alerts
5. ✅ Setup automated backups for PostgreSQL
6. ✅ Test push notifications end-to-end
