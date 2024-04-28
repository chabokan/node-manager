#!/bin/bash

cd /var/ch-manager
sudo git reset --hard HEAD && sudo git pull
alembic upgrade head

docker compose build web
docker compose up -d
docker compose restart web

pip install -r requirements.txt

curl -s https://raw.githubusercontent.com/chabokan/server-connector/main/firewall.sh | bash

service cron restart
