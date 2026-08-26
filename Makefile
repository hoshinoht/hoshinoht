PY ?= python3

.PHONY: all sync build check clean

all: build check

sync:
	bash scripts/sync-index.sh

build:
	$(PY) scripts/build_readme.py

check:
	$(PY) scripts/check_svgs.py

clean:
	rm -rf __pycache__ scripts/__pycache__
