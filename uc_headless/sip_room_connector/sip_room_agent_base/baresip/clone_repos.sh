#!/bin/bash
cd "$(dirname "$0")"
set -e
git -C rem fetch || git clone https://github.com/creytiv/rem.git 
git -C rem checkout 6059cd581730ebe28a5af59d767a383a6c701757

git -C libre fetch || git clone git@github.com:dialpad/libre.git
git -C libre checkout fcf77b1459525222899e765cae7d84d64cf62419

git -C baresip fetch || git clone git@github.com:dialpad/baresip.git
git -C baresip checkout 86b93bf9e7a2bef57202aeef3128d6dbf5464602