#!/bin/bash
cd "$(dirname "$0")"
export P_CACHE_PATH="/Users/news/Desktop/Projects/ElectionData/Development/temp/p_cache.json"
exec gunicorn -w 1 -b 0.0.0.0:8004 app:app
