#!/bin/bash
set -e
echo "Запуск контейнеров..."
docker compose build
docker compose up -d
echo "Ожидание готовности БД..."
sleep 10
docker compose exec -T web python manage.py migrate --noinput
docker compose exec -T web python manage.py collectstatic --noinput
echo ""
echo "============================================="
echo "Сервер AFM запущен на http://192.168.0.180"
echo "Создайте суперпользователя командой:"
echo "docker compose exec web python manage.py createsuperuser"
echo "============================================="
