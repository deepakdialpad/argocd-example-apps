#!/bin/bash
cd "$(dirname "$0")"
set -e
WORKFLOW_FILE="../../../../.github/workflows/dm-sip-room-agent-base.yaml"
BARESIP_COMMIT=`sed $WORKFLOW_FILE -n -e "s/^.*BARESIP_COMMIT:\s*\(\S*\).*$/\1/p"`
REM_COMMIT=`sed $WORKFLOW_FILE -n -e "s/^.*REM_COMMIT:\s*\(\S*\).*$/\1/p"`
LIBRE_COMMIT=`sed $WORKFLOW_FILE -n -e "s/^.*LIBRE_COMMIT:\s*\(\S*\).*$/\1/p"`

git -C rem fetch || git clone https://github.com/creytiv/rem.git 
git -C rem checkout $REM_COMMIT

git -C libre fetch || git clone git@github.com:dialpad/libre.git
git -C libre checkout $LIBRE_COMMIT

git -C baresip fetch || git clone git@github.com:dialpad/baresip.git
git -C baresip checkout $BARESIP_COMMIT