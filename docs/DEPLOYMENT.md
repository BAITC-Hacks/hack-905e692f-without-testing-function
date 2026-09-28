# Production deployment — Ubuntu 22.04 LTS VPS

## GovTech Camp (authoritative deployment path)

Public URL: `https://without-testing-function.govtech-kz.com`. GovTech manages the external Caddy, HTTPS and certificates; this project must not bind host `80/443`, configure Caddy, or use Certbot. The only published application port is `8024:8080`; FastAPI (`8000`) and Next.js (`3000`) are internal-only.

```bash
docker compose -f docker-compose.prod.yml config
docker compose -f docker-compose.prod.yml build
docker compose -f docker-compose.prod.yml up -d
docker compose -f docker-compose.prod.yml ps
docker compose -f docker-compose.prod.yml logs --tail=100
curl --fail --show-error http://127.0.0.1:8024/api/v1/health
curl --fail --show-error https://without-testing-function.govtech-kz.com/api/v1/health
```

Stop only this deployment with `docker compose -f docker-compose.prod.yml down`; inspect it with `docker compose -f docker-compose.prod.yml logs -f`. After `git pull --ff-only`, run `config`, `build`, then `up -d` again. For rollback, check out a known-good commit, rebuild, `up -d`, and verify the local health endpoint.

`data/processed/` and `ml/artifacts/` contain the published mart and model artifact mounted read-only at runtime. The `pipeline` profile is intentionally separate and never starts with ordinary production startup. Do not put raw data into HTTP paths or run ingestion/training on the shared VPS unless explicitly required.

If artifacts need delivery through the private S3-compatible bucket, authenticate outside this repository and copy only the approved processed/model artifacts. Do not put access keys in `.env`, Git, shell history, or logs. A missing or invalid artifact is reported by health as `MODEL_NOT_READY`/`DATA_UNAVAILABLE`.

Troubleshooting: for `502`, first check `docker compose -f docker-compose.prod.yml ps` and service logs; for `unhealthy`, query the internal API health command above and inspect only the affected service. If `http://127.0.0.1:8024` works but the public URL fails, the application deployment is healthy and GovTech external routing needs attention; do not change system Caddy.

Замените `example.gov.kz`, `YOUR_SERVER_IP` и repository URL своими значениями. Не вставляйте secrets в shell history или Git.

## 1. VPS prerequisites

Ubuntu 22.04 LTS, 2 vCPU, 4 GB RAM, 20+ GB SSD, публичный IPv4, sudo-доступ и домен. Диск должен вмещать raw snapshot, параллельные mart/model versions и backups.

```bash
ssh <current-sudo-user>@YOUR_SERVER_IP
```

Держите эту сессию открытой до окончания SSH hardening.

## 2. Создание deploy user

```bash
sudo adduser --disabled-password --gecos '' medflow-deploy
sudo usermod -aG sudo medflow-deploy
sudo install -d -m 700 -o medflow-deploy -g medflow-deploy /home/medflow-deploy/.ssh
sudo cp ~/.ssh/authorized_keys /home/medflow-deploy/.ssh/authorized_keys
sudo chown medflow-deploy:medflow-deploy /home/medflow-deploy/.ssh/authorized_keys
sudo chmod 600 /home/medflow-deploy/.ssh/authorized_keys
```

## 3. SSH key authentication

Во **втором** terminal сначала проверьте key login:

```bash
ssh medflow-deploy@YOUR_SERVER_IP
sudo -v
```

Только после успеха отключайте password/root login:

```bash
sudo tee /etc/ssh/sshd_config.d/99-medflow.conf >/dev/null <<'EOF'
PasswordAuthentication no
KbdInteractiveAuthentication no
PermitRootLogin no
EOF
sudo sshd -t
sudo systemctl reload ssh
```

Проверьте ещё одну новую SSH-сессию. При ошибке из исходной открытой сессии: `sudo rm /etc/ssh/sshd_config.d/99-medflow.conf && sudo systemctl reload ssh`.

## 4. Обновление ОС

```bash
sudo apt update
sudo apt full-upgrade -y
sudo apt install -y ca-certificates curl gnupg git ufw fail2ban unattended-upgrades certbot acl
sudo dpkg-reconfigure -plow unattended-upgrades
sudo systemctl enable --now fail2ban
```

После обновления ядра выполните `sudo reboot` и снова войдите ключом.

## 5. Firewall

Сначала разрешите SSH, затем включайте UFW:

```bash
sudo ufw default deny incoming
sudo ufw default allow outgoing
sudo ufw allow OpenSSH
sudo ufw allow 80/tcp
sudo ufw allow 443/tcp
sudo ufw show added
sudo ufw enable
sudo ufw status verbose
```

Порты 3000, 8000 и SQLite наружу не публикуются.

## 6. Docker Engine и Compose

```bash
sudo install -m 0755 -d /etc/apt/keyrings
curl -fsSL https://download.docker.com/linux/ubuntu/gpg | sudo gpg --dearmor -o /etc/apt/keyrings/docker.gpg
sudo chmod a+r /etc/apt/keyrings/docker.gpg
echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" | sudo tee /etc/apt/sources.list.d/docker.list >/dev/null
sudo apt update
sudo apt install -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
sudo systemctl enable --now docker
sudo usermod -aG docker medflow-deploy
```

Выйдите и войдите снова:

```bash
docker --version
docker compose version
docker run --rm hello-world
```

Группа `docker` эквивалентна root-доступу; не добавляйте лишних пользователей.

## 7. Clone project

```bash
sudo install -d -o medflow-deploy -g medflow-deploy /opt/medflow
git clone <REPOSITORY_URL> /opt/medflow
cd /opt/medflow
git status --short
```

Images фиксируют Python 3.12.7 и Node 20.18.1. Host Node не нужен. Для host build требуется Node ≥20.9.0; Node 18 не поддерживается.

## 8. Environment и secrets

```bash
cp .env.example .env
chmod 600 .env
nano .env
```

```dotenv
APP_ENV=production
DOMAIN=example.gov.kz
CORS_ORIGINS=https://example.gov.kz
TRUSTED_HOSTS=example.gov.kz
MAX_REQUEST_BYTES=1048576
ENABLE_DEMO_AUTH=false
MEDFLOW_DB=/app/state/app.sqlite3
ADMIN_EMAIL=admin@example.gov.kz
ADMIN_PASSWORD_HASH='$argon2id$REPLACE_WITH_GENERATED_HASH'
```

Создайте hash интерактивно:

```bash
docker compose -f docker-compose.prod.yml --profile pipeline build pipeline
docker compose -f docker-compose.prod.yml --profile pipeline run --rm pipeline python apps/api/scripts/generate_password_hash.py
```

В `.env` вставьте только hash. Одинарные кавычки защищают `$`. `stat -c '%a %n' .env` должен показать `600`.

## 9. Data placement

Raw не хранится в Git/image. Передайте разрешённый snapshot защищённо:

```bash
scp '<LOCAL_PATH_TO_WAITING_CSV>' medflow-deploy@YOUR_SERVER_IP:/tmp/waiting.csv
```

На VPS:

```bash
cd /opt/medflow
install -m 640 /tmp/waiting.csv 'data/raw/Ожидающие плановую госпитализацию в стационары.csv'
rm /tmp/waiting.csv
sudo install -d -o 10001 -g 10001 data/interim data/processed ml/artifacts
sudo chown -R 10001:10001 data/interim data/processed ml/artifacts
sudo chmod -R u=rwX,g=rX,o= data/raw data/interim data/processed ml/artifacts
```

Не используйте `chmod 777`. Ingestion ожидает ровно один `*Ожидающие плановую*.csv`.

## 10. Ingestion

```bash
docker compose -f docker-compose.prod.yml --profile pipeline run --rm pipeline
cat data/interim/ingestion_state.json
cat data/processed/current_mart.json
```

Неизменившийся snapshot пропускается; новый создаёт version и атомарно переключает pointer после validation.

## 11. Model training / artifact

```bash
docker compose -f docker-compose.prod.yml --profile pipeline run --rm pipeline python -m app.cli train
docker compose -f docker-compose.prod.yml --profile pipeline run --rm pipeline python -m app.cli status
cat ml/artifacts/current_model.json
```

Matching artifact переиспользуется. Forced retrain: `python -m app.cli train --force`. Training не запускается HTTP-запросом.

## 12. Production build

```bash
docker compose -f docker-compose.prod.yml config
docker compose -f docker-compose.prod.yml build --pull api frontend
```

API/frontend read-only, работают как UID 10001, без Linux capabilities и во внутренней network.

## 13. Nginx

`deploy/nginx/default.conf.template` делает HTTP→HTTPS redirect, проксирует `/api/` в FastAPI и `/` в Next.js, rate-limits login и добавляет security headers. Не запускайте второй host Nginx на 80/443.

## 14. Domain

Создайте DNS:

- `A example.gov.kz → YOUR_SERVER_IP`;
- `AAAA` только при рабочем публичном IPv6.

```bash
dig +short A example.gov.kz
dig +short AAAA example.gov.kz
```

Ошибочный AAAA приведёт часть клиентов к недоступному IPv6.

## 15. HTTPS

Получайте certificate только после корректного DNS и открытого 80. До старта Nginx:

```bash
sudo certbot certonly --standalone -d example.gov.kz
sudo setfacl -Rm u:101:rX /etc/letsencrypt/live /etc/letsencrypt/archive
sudo certbot renew --dry-run
docker compose -f docker-compose.prod.yml up -d
docker compose -f docker-compose.prod.yml ps
```

UID 101 — unprivileged Nginx image; ACL даёт read/traverse. После renewal: `docker compose -f docker-compose.prod.yml exec nginx nginx -s reload`. Настройте это как Certbot deploy hook после dry-run.

## 16. Health verification

```bash
curl -I http://example.gov.kz
curl --fail --show-error https://example.gov.kz/api/v1/health
curl -I https://example.gov.kz
openssl s_client -connect example.gov.kz:443 -servername example.gov.kz </dev/null 2>/dev/null | openssl x509 -noout -dates -issuer
docker compose -f docker-compose.prod.yml ps
```

Processed data, model и database должны быть available/ready. `/docs` в production выключен.

## 17. Backups

Зашифрованно и off-host сохраняйте:

- application SQLite volume;
- `data/processed/current_mart.json` и referenced `mart_versions/`;
- `ml/artifacts/current_model.json` и `versions/`;
- ingestion/model metadata и checksums;
- configs; `.env` только в отдельном secret backup;
- raw только по retention policy владельца данных.

Команды online backup/restore: [BACKUP_RESTORE.md](BACKUP_RESTORE.md). Проверяйте restore ежеквартально.

## 18. Update deployment

```bash
cd /opt/medflow
git fetch --all --prune
git status --short
git pull --ff-only
docker compose -f docker-compose.prod.yml config
docker compose -f docker-compose.prod.yml build --pull api frontend
docker compose -f docker-compose.prod.yml up -d --no-deps api frontend
docker compose -f docker-compose.prod.yml ps
curl --fail https://example.gov.kz/api/v1/health
```

Migration framework отсутствует: SQLite tables создаются idempotently. Ingestion/training запускайте при новом source/schema или осознанном retrain. После новой публикации: `docker compose -f docker-compose.prod.yml restart api`.

## 19. Rollback

Перед update сохраните commit и pointers:

```bash
git rev-parse HEAD
cp data/processed/current_mart.json /tmp/current_mart.before-update.json
cp ml/artifacts/current_model.json /tmp/current_model.before-update.json
```

Application rollback: checkout проверенного tag/commit и rebuild. Для data/model сначала проверьте referenced directories/metadata/checksum, затем атомарно верните pointers и restart:

```bash
cp /tmp/current_mart.before-update.json data/processed/.current_mart.rollback
mv data/processed/.current_mart.rollback data/processed/current_mart.json
cp /tmp/current_model.before-update.json ml/artifacts/.current_model.rollback
mv ml/artifacts/.current_model.rollback ml/artifacts/current_model.json
docker compose -f docker-compose.prod.yml restart api
curl --fail https://example.gov.kz/api/v1/health
```

Не редактируйте published version in place и не удаляйте последнюю рабочую version до проверенного backup.

## 20. Logs / debugging

```bash
docker compose -f docker-compose.prod.yml logs --tail=200 nginx
docker compose -f docker-compose.prod.yml logs --tail=200 api
docker compose -f docker-compose.prod.yml logs --tail=200 frontend
docker compose -f docker-compose.prod.yml logs -f --since=10m
sudo journalctl -u docker --since '30 minutes ago'
```

Не публикуйте logs, `.env`, bearer tokens или raw rows в issue tracker.

## Troubleshooting

### Node version error

`Node.js version ">=20.9.0" is required` означает Node 18 + Next.js 16.3.6.

```bash
cd /opt/medflow
nvm install
nvm use
node --version
cd frontend && npm ci && npm run build
```

Production Docker уже использует Node 20.18.1; не downgrade Next.js.

### ModuleNotFoundError: app

Package находится в `apps/api/app` и устанавливается через `pyproject.toml`:

```bash
cd /opt/medflow
source .venv/bin/activate
python -m pip install -e .
python -c 'import app; print(app.__file__)'
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

### MODEL_NOT_READY

```bash
cat ml/artifacts/current_model.json
find ml/artifacts/versions -maxdepth 2 -type f -ls
docker compose -f docker-compose.prod.yml --profile pipeline run --rm pipeline python -m app.cli status
docker compose -f docker-compose.prod.yml --profile pipeline run --rm pipeline python -m app.cli train
docker compose -f docker-compose.prod.yml logs --tail=200 api
```

Причины: artifact отсутствует, checksum/schema повреждены или fingerprint не совпадает. Верните matching version/retrain и restart API.

### Dataset unavailable

```bash
find data/raw -maxdepth 1 -type f -printf '%f %s bytes\n'
cat data/interim/ingestion_state.json
cat data/processed/current_mart.json
docker compose -f docker-compose.prod.yml --profile pipeline run --rm pipeline
```

Проверьте один matching CSV, required columns и права UID 10001.

### API unavailable

```bash
docker compose -f docker-compose.prod.yml ps api
docker compose -f docker-compose.prod.yml logs --tail=200 api
docker compose -f docker-compose.prod.yml exec api python -c "import urllib.request; print(urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health').read().decode())"
curl -vk https://example.gov.kz/api/v1/health
```

### 502 Bad Gateway

1. `docker compose ... ps nginx api frontend`;
2. `docker compose ... logs nginx` — определите upstream;
3. `docker compose ... exec nginx wget -qO- http://api:8000/api/v1/health`;
4. проверьте API health/logs и internal network;
5. аналогично проверьте `http://frontend:3000`;
6. restart только затронутого service.

### CORS

`CORS_ORIGINS` должен содержать точный origin без path: `https://example.gov.kz`. После изменения: `docker compose -f docker-compose.prod.yml up -d --force-recreate api`. CORS не исправляет `TRUSTED_HOSTS` или auth.

### Permission denied

```bash
namei -l data/processed/current_mart.json
find data/interim data/processed ml/artifacts -maxdepth 2 -printf '%u:%g %m %p\n' | head -50
sudo chown -R 10001:10001 data/interim data/processed ml/artifacts
sudo chmod -R u=rwX,g=rX,o= data/interim data/processed ml/artifacts
```

Не используйте `chmod 777`; raw остаётся read-only для pipeline.

### Disk full

```bash
df -h
df -i
sudo du -xhd1 /var/lib/docker /opt/medflow 2>/dev/null
docker system df
journalctl --disk-usage
```

Удаляйте только подтверждённые старые backups/logs/unused images и не-current versions после проверки retention. Не запускайте без проверки `docker system prune -a`.
