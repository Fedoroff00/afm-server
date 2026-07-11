#!/usr/bin/env bash
# ============================================================================
# Astra File Monitor – универсальный скрипт развёртывания серверной части
# ============================================================================
# Поддерживаемые ОС: Debian 11/12, Ubuntu 20.04/22.04/24.04, Astra Linux SE 1.7
# Запуск: sudo bash deploy.sh
# ============================================================================
set -euo pipefail

# ----------------------------- Проверка прав -----------------------------
if [[ "$(id -u)" -ne 0 ]]; then
    echo "❌ Запустите скрипт от root: sudo bash deploy.sh"
    exit 1
fi

# ----------------------------- Определение IP -----------------------------
SERVER_IP=$(hostname -I 2>/dev/null | awk '{print $1}')
if [[ -z "$SERVER_IP" ]]; then
    echo "Не удалось определить IP-адрес автоматически."
    echo "Введите IP-адрес, на котором будет доступен сервер:"
    read -r SERVER_IP
fi
if [[ -z "$SERVER_IP" ]]; then
    echo "❌ IP-адрес не указан. Выход."
    exit 1
fi

echo ""
echo "==============================================="
echo "  Astra File Monitor – установка сервера"
echo "==============================================="
echo "Сервер будет доступен по адресу: http://${SERVER_IP}"
echo ""

# ----------------------------- Зависимости -------------------------------
echo "▶ Установка системных зависимостей (curl, git)..."
apt-get update -qq
apt-get install -y -qq curl git

# ----------------------------- Docker ------------------------------------
if ! command -v docker &>/dev/null; then
    echo "▶ Установка Docker..."
    curl -fsSL https://get.docker.com | bash
    systemctl enable --now docker
else
    echo "✓ Docker уже установлен"
fi

# Убедимся, что docker compose (плагин) доступен
if ! docker compose version &>/dev/null; then
    echo "▶ Установка Docker Compose плагина..."
    apt-get install -y -qq docker-compose-plugin
fi

# ----------------------------- Клонирование / обновление проекта ---------
PROJECT_DIR="/opt/afm-server"
if [[ -d "$PROJECT_DIR/.git" ]]; then
    echo "▶ Репозиторий найден, обновляем..."
    cd "$PROJECT_DIR"
    git pull --ff-only
else
    echo "▶ Клонирование репозитория..."
    read -p "Введите URL репозитория (или нажмите Enter для ручного копирования): " REPO_URL
    if [[ -n "$REPO_URL" ]]; then
        git clone "$REPO_URL" "$PROJECT_DIR"
    else
        # Ручное копирование из текущей папки (если скрипт уже внутри проекта)
        if [[ -f "docker-compose.yml" ]] && [[ -f "deploy.sh" ]]; then
            echo "Копируем текущий проект в $PROJECT_DIR ..."
            mkdir -p "$PROJECT_DIR"
            cp -r . "$PROJECT_DIR"
        else
            echo "❌ Не найден проект. Укажите корректный URL репозитория."
            exit 1
        fi
    fi
fi

cd "$PROJECT_DIR"

# ----------------------------- .env -------------------------------------
if [[ ! -f ".env" ]]; then
    echo "▶ Генерация секретов и файла .env ..."
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
    echo "✓ .env создан"
else
    echo "✓ .env уже существует, пропускаем"
fi

# ----------------------------- Запуск контейнеров ------------------------
echo "▶ Сборка и запуск Docker-контейнеров..."
docker compose build --pull
docker compose up -d

# ----------------------------- Ожидание готовности БД -------------------
echo "▶ Ожидание готовности PostgreSQL..."
RETRIES=12
until docker compose exec -T db pg_isready -U afm &>/dev/null; do
    sleep 5
    RETRIES=$((RETRIES - 1))
    if [[ $RETRIES -eq 0 ]]; then
        echo "❌ PostgreSQL не запустился за отведённое время."
        exit 1
    fi
    echo "   ждём..."
done

# ----------------------------- Миграции и статика -----------------------
echo "▶ Применение миграций и сбор статики..."
docker compose exec -T web python manage.py migrate --noinput
docker compose exec -T web python manage.py collectstatic --noinput

# ----------------------------- Создание суперпользователя ---------------
echo ""
echo "==============================================="
echo "  Создание администратора"
echo "==============================================="
echo "Введите данные для первого администратора веб-интерфейса:"
docker compose exec -it web python manage.py createsuperuser

# ----------------------------- Копирование deb-пакета агента -----------
echo ""
echo "▶ Подготовка deb-пакета агента для скачивания..."
mkdir -p backend/media/packages
if [[ -f "astra-monitor-agent_1.0.0_all.deb" ]]; then
    cp astra-monitor-agent_1.0.0_all.deb backend/media/packages/astra-monitor-agent_latest_all.deb
    docker compose exec -T web chmod 644 /app/media/packages/astra-monitor-agent_latest_all.deb 2>/dev/null || true
    echo "✓ DEB-пакет скопирован"
else
    echo "⚠ DEB-пакет агента не найден (astra-monitor-agent_1.0.0_all.deb)."
    echo "  Вы сможете загрузить его позже в админ-панели."
fi

# ----------------------------- Готово ----------------------------------
echo ""
echo "==============================================="
echo "   🎉 Сервер Astra File Monitor запущен!"
echo "==============================================="
echo "Веб-интерфейс:        http://${SERVER_IP}"
echo "Админ-панель Django:  http://${SERVER_IP}/admin/"
echo ""
echo "Добавление агентов:"
echo "  1. Откройте http://${SERVER_IP}/machines/"
echo "  2. Нажмите «Добавить агента», введите хостнейм и IP"
echo "  3. Скопируйте команду установки и выполните её на целевой машине"
echo ""
echo "Скачать DEB-пакет агента:"
echo "  http://${SERVER_IP}/media/packages/astra-monitor-agent_latest_all.deb"
echo ""
echo "Для остановки сервера:  cd ${PROJECT_DIR} && docker compose down"
echo "==============================================="
