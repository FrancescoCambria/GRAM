#!/bin/sh
# Replace ${BASE_PATH} in the nginx template and output to the real config
envsubst '${BASE_PATH}' < /etc/nginx/conf.d/default.conf.template > /etc/nginx/conf.d/default.conf

# Execute the CMD from the Dockerfile
exec "$@"
