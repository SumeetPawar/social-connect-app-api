# Deployment Guide - Fitness Tracker API

## Option 1: Docker Deployment (Recommended)

### Prerequisites
- Docker and Docker Compose installed
- Domain name (optional, for HTTPS)

### Steps

1. **Set Environment Variables**
   ```bash
   export JWT_SECRET=$(python -c "import secrets; print(secrets.token_urlsafe(32))")
   ```

2. **Build and Run**
   ```bash
   docker-compose -f docker-compose.prod.yml up -d
   ```

3. **Check Logs**
   ```bash
   docker-compose -f docker-compose.prod.yml logs -f app
   ```

4. **Stop**
   ```bash
   docker-compose -f docker-compose.prod.yml down
   ```

---

## Option 2: VPS Deployment (Ubuntu/Debian)

### Prerequisites
- Ubuntu 20.04+ or Debian 11+ server
- Root or sudo access
- Domain name pointed to your server IP

### Steps

1. **Update System**
   ```bash
   sudo apt update && sudo apt upgrade -y
   ```

2. **Install Dependencies**
   ```bash
   sudo apt install -y python3.11 python3.11-venv nginx postgresql postgresql-contrib certbot python3-certbot-nginx
   ```

3. **Setup PostgreSQL**
   ```bash
   sudo -u postgres psql
   CREATE DATABASE fitness_tracker;
   CREATE USER fitness_user WITH PASSWORD 'your-secure-password';
   GRANT ALL PRIVILEGES ON DATABASE fitness_tracker TO fitness_user;
   \q
   ```

4. **Clone Repository**
   ```bash
   sudo mkdir -p /var/www
   cd /var/www
   sudo git clone <your-repo-url> fitness-tracker-api
   cd fitness-tracker-api
   sudo chown -R www-data:www-data /var/www/fitness-tracker-api
   ```

5. **Setup Python Environment**
   ```bash
   sudo -u www-data python3.11 -m venv .venv
   sudo -u www-data .venv/bin/pip install -r requirements.txt
   sudo -u www-data .venv/bin/pip install gunicorn
   ```

6. **Create .env File**
   ```bash
   sudo -u www-data nano .env
   ```
   Add:
   ```
   DATABASE_URL=postgresql+asyncpg://fitness_user:your-secure-password@localhost/fitness_tracker
   JWT_SECRET=your-generated-secret-key
   ENV=production
   ```

7. **Run Migrations**
   ```bash
   sudo -u www-data .venv/bin/alembic upgrade head
   ```

8. **Setup Systemd Service**
   ```bash
   sudo cp fitness-tracker.service /etc/systemd/system/
   sudo mkdir -p /var/log/fitness-tracker
   sudo chown www-data:www-data /var/log/fitness-tracker
   sudo systemctl daemon-reload
   sudo systemctl enable fitness-tracker
   sudo systemctl start fitness-tracker
   sudo systemctl status fitness-tracker
   ```

9. **Setup Nginx**
   ```bash
   sudo cp nginx.conf /etc/nginx/sites-available/fitness-tracker
   # Edit the file and change 'your-domain.com' to your actual domain
   sudo nano /etc/nginx/sites-available/fitness-tracker
   sudo ln -s /etc/nginx/sites-available/fitness-tracker /etc/nginx/sites-enabled/
   sudo nginx -t
   sudo systemctl restart nginx
   ```

10. **Setup SSL (Let's Encrypt)**
    ```bash
    sudo certbot --nginx -d your-domain.com
    ```

---

## Option 3: Cloud Platforms

### AWS ECS / Azure Container Apps / Google Cloud Run

1. Build Docker image:
   ```bash
   docker build -t fitness-tracker-api .
   ```

2. Push to container registry (ECR, ACR, GCR)

3. Deploy using platform-specific tools

### Heroku

1. Install Heroku CLI
2. Create Procfile:
   ```
   web: gunicorn app.main:app --workers 4 --worker-class uvicorn.workers.UvicornWorker --bind 0.0.0.0:$PORT
   release: alembic upgrade head
   ```
3. Deploy:
   ```bash
   heroku create
   heroku addons:create heroku-postgresql:mini
   heroku config:set JWT_SECRET=$(python -c "import secrets; print(secrets.token_urlsafe(32))")
   git push heroku main
   ```

---

## Post-Deployment Checklist

- [ ] Database backups configured
- [ ] Monitoring setup (Sentry, Datadog, etc.)
- [ ] Log rotation configured
- [ ] Firewall rules set (allow 80, 443, SSH only)
- [ ] Environment variables secured
- [ ] VAPID keys moved to environment variables
- [ ] CORS origins updated for production domain
- [ ] SSL certificate auto-renewal tested
- [ ] Scheduler running (check logs for hourly reminders)

---

## Useful Commands

**Check app logs:**
```bash
# Docker
docker-compose logs -f app

# Systemd
sudo journalctl -u fitness-tracker -f
```

**Restart app:**
```bash
# Docker
docker-compose restart app

# Systemd
sudo systemctl restart fitness-tracker
```

**Run migrations:**
```bash
# Docker
docker-compose exec app alembic upgrade head

# Systemd
cd /var/www/fitness-tracker-api && sudo -u www-data .venv/bin/alembic upgrade head
```

**Test push notifications:**
```bash
# Docker
docker-compose exec app python test_reminders.py

# Systemd
cd /var/www/fitness-tracker-api && sudo -u www-data .venv/bin/python test_reminders.py
```
