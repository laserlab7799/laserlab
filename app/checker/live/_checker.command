#!/bin/bash
cd "$(dirname "$0")"
exec gunicorn -k uvicorn.workers.UvicornWorker -w 1 -b 0.0.0.0:9053 app:app
