#!/bin/bash

if [ -d /app/supervisor_conf ]; then
  echo "> Copy Supervisor Configuration Directory ..."
  cp -rf /app/supervisor_conf/* /etc/supervisor/conf.d/
  echo "> Starting Supervisor ..."
  for i in {1..5}; do {
    service supervisor start
    supervisorctl reread
    supervisorctl update
    supervisorctl start all
  } && break || sleep 5; done
else
  if [ -f /app/supervisor.conf ]; then
    echo "> Copy Supervisor Configuration ..."
    cp -rf /app/supervisor.conf /etc/supervisor/conf.d/supervisor.conf
    echo "> Starting Supervisor ..."
    for i in {1..5}; do {
      service supervisor start
      supervisorctl reread
      supervisorctl update
      supervisorctl start all
    } && break || sleep 5; done
  fi
fi

alembic upgrade head

echo "Run uvicorn Server"
uvicorn "main:app" --host 0.0.0.0 --port 80 --workers 1 --log-level info --reload
