#!/bin/bash
# Скрипт для смены IP-адреса сервера AFM после переезда в другую сеть
set -e

if [ "$(id -u)" -ne 0 ]; then
    echo "Запустите от root: sudo bash change_ip.sh"
    exit 1
fi

CURRENT_IP=$(hostname -I | awk '{print $1}')
if [ -z "$CURRENT_IP" ]; then
    read -p "Введите новый IP-адрес сервера: " CURRENT_IP
fi

echo "Новый IP-адрес: $CURRENT_IP"

if [ -f .env ]; then
    sed -i "s/ALLOWED_HOSTS=.*/ALLOWED_HOSTS=$CURRENT_IP,localhost,127.0.0.1,web/" .env
else
    echo "Файл .env не найден. Убедитесь, что вы находитесь в папке проекта."
    exit 1
fi

docker compose down
docker compose up -d

echo ""
echo "============================================="
echo "IP-адрес сервера успешно изменён!"
echo "Новый адрес: http://$CURRENT_IP"
echo ""
echo "Не забудьте обновить server_url в конфигурации агентов"
echo "/etc/astra-monitor/config.yaml и перезапустить их:"
echo "  sudo systemctl restart astra-monitor"
echo "============================================="
