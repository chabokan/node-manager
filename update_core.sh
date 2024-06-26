#!/bin/bash

cd /var/ch-manager

sudo git reset --hard HEAD && sudo git pull
alembic upgrade head

docker pull docker.chabokan.net/chabokan/node-manager
docker compose up -d
docker compose restart web

pip install -r requirements.txt

curl -s https://raw.githubusercontent.com/chabokan/server-connector/main/utilities/firewall.sh | bash

service cron restart
