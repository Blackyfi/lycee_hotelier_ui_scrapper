#!/usr/bin/env bash
# Project verify gate. Failures bubble up — never `|| true` here, the whole
# point is for CI / automated agents to get an honest signal.
set -euo pipefail

ruff check .
pytest -q
