#!/bin/bash

set -e  # Exit on error

# Loop through all requirements.txt files in agents/*/ and api_library/*/
for req_file in agents/*/requirements.txt api_library/*/requirements.txt; do
  if [ -f "$req_file" ]; then
    echo "Installing from $req_file..."
    pip install -r "$req_file"
  fi
done
