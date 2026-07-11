#!/usr/bin/env bash
# Универсальный скрипт развёртывания Astra File Monitor
set -euo pipefail

if [[ "$(id -u)" -ne 0 ]]; then
    echo "Запустите от root: sudo bash deploy.sh"
    exit 1
fi

SERVER_IP=$(hostname -I 2>/dev/null | awk '{print $1}')
if [[ -z "$SERVER_IP" ]]; then
    read -p "Введите IP-адрес сервера: " SERVER_IP
fi

echo ""
echo "==============================================="
echo "  Astra File Monitor – установка сервера"
echo "  Сервер будет доступен по http://${SERVER_IP}"
echo "==============================================="

# Установка зависимостей
apt-get update -qq
apt-get install -y -qq curl git

# Docker
if ! command -v docker &>/dev/null; then
    curl -fsSL https://get.docker.com | bash
    systemctl enable --now docker
fi
if ! docker compose version &>/dev/null; then
    apt-get install -y -qq docker-compose-plugin
fi

# Клонирование / копирование проекта
PROJECT_DIR="/opt/afm-server"
if [[ -d "$PROJECT_DIR/.git" ]]; then
    cd "$PROJECT_DIR"
    git pull --ff-only
else
    if [[ -f "docker-compose.yml" ]]; then
        mkdir -p "$PROJECT_DIR"
        cp -r . "$PROJECT_DIR"
    else
        read -p "URL репозитория: " REPO_URL
        git clone "$REPO_URL" "$PROJECT_DIR"
    fi
fi
cd "$PROJECT_DIR"

# .env с полным набором переменных
if [[ ! -f ".env" ]]; then
    SECRET_KEY=$(openssl rand -base64 48)
    DB_PASSWORD=$(openssl rand -base64 24)
    cat > .env << EOF
SECRET_KEY=${SECRET_KEY}
DEBUG=False
ALLOWED_HOSTS=${SERVER_IP},localhost,127.0.0.1,web
DB_PASSWORD=${DB_PASSWORD}
REDIS_URL=redis://redis:6379/0
CELERY_BROKER_URL=redis://redis:6379/0
EMAIL_HOST=
EMAIL_PORT=587
EMAIL_HOST_USER=
EMAIL_HOST_PASSWORD=
DEFAULT_FROM_EMAIL=
ADMIN_EMAILS=
WEBHOOK_URL=
TOTP_ISSUER=AFM
EOF
fi

# Запуск контейнеров
docker compose build --pull
docker compose up -d

# Ожидание готовности БД
RETRIES=12
until docker compose exec -T db pg_isready -U afm &>/dev/null; do
    sleep 5
    ((RETRIES--)) || { echo "PostgreSQL не запустился"; exit 1; }
done

# Миграции и статика
docker compose exec -T web python manage.py migrate --noinput
docker compose exec -T web python manage.py collectstatic --noinput

# Копирование deb-пакета агента в том
mkdir -p backend/media/packages
if [[ -f astra-monitor-agent_2.0.0_all.deb ]]; then
    docker compose exec -T web mkdir -p /app/media/packages
    docker cp astra-monitor-agent_2.0.0_all.deb afm-server-web-1:/app/media/packages/astra-monitor-agent_latest_all.deb
    echo "DEB-пакет агента скопирован в контейнер"
else
    echo "DEB-пакет агента не найден. Вы сможете загрузить его позже."
fi

# Создание суперпользователя
echo "Создайте администратора:"
docker compose exec -it web python manage.py createsuperuser

echo ""
echo "Готово! Веб-интерфейс: http://${SERVER_IP}"
echo "Скачать агента: http://${SERVER_IP}/download-agent/"
