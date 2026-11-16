#!/usr/bin/env bash
# Downloads the two source datasets into data/raw/.
# Prerequisites:
#   1. Kaggle account + API token at ~/.kaggle/kaggle.json (Kaggle > Settings > Create New Token)
#   2. Accept the M5 competition rules once in the browser:
#      https://www.kaggle.com/competitions/m5-forecasting-accuracy/rules
set -euo pipefail

mkdir -p data/raw/m5 data/raw/dataco

kaggle competitions download -c m5-forecasting-accuracy -p data/raw/m5
unzip -o data/raw/m5/m5-forecasting-accuracy.zip -d data/raw/m5

kaggle datasets download -d shashwatwork/dataco-smart-supply-chain-for-big-data-analysis \
  -p data/raw/dataco --unzip

echo "Done. Files:"
ls -lh data/raw/m5 data/raw/dataco
