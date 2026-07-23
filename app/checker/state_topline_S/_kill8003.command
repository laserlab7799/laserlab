#!/bin/bash
# kill whatever is bound to port 8050
kill -15 $(lsof -t -i :8003) 2>/dev/null
