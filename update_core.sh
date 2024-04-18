#!/bin/bash

cd /var/manager
sudo git reset --hard HEAD && sudo git pull
docker compose build web
docker compose up -d
docker compose restart web

pip install -r /app/requirements.txt

service cron restart
