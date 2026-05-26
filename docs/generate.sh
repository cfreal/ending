#!/bin/bash

DIRECTORY=$(dirname -- "$0")

cd "$DIRECTORY"

rm -rf ./pdoc
python3 -m mkdocs build
python3 -m pdoc ../ending -o site/pdoc --no-show-source

echo
echo
echo ---------------------------------
echo Documentation is available at: $PWD/site/index.html
echo ---------------------------------
