#!/bin/bash

cd /var/manager
sudo git reset --hard HEAD && sudo git pull
docker compose build web
docker compose up -d
docker compose restart web

service supervisor stop
sleep 3
service supervisor start
sleep 5
supervisorctl reread
supervisorctl update
supervisorctl start all