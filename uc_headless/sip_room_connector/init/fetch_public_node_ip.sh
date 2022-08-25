#!/bin/bash

if [ -f "$APP_ENV_CONFIG_FILE" ]; then
    echo "Found existing config $APP_ENV_CONFIG_FILE:"
    cat "$APP_ENV_CONFIG_FILE"
else
    PUBLIC_NODE_IP=$(curl "$GCE_METADATA_SERVER_EXTERNAL_IP_ENDPOINT" -H "Metadata-Flavor: Google")
    echo -e "Fetched external Node IP: $PUBLIC_NODE_IP\nWriting to env file $APP_ENV_CONFIG_FILE"
    echo "$PUBLIC_NODE_IP_VAR_NAME=$PUBLIC_NODE_IP" > "$APP_ENV_CONFIG_FILE"
fi
