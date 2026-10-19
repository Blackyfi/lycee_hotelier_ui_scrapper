#!/usr/bin/env bash
set -euo pipefail

args=(
  uvicorn
  app.main:app
  --host
  0.0.0.0
  --port
  8000
)

cert_file="${TLS_CERT_FILE:-}"
key_file="${TLS_KEY_FILE:-}"

if [[ -n "${cert_file}" || -n "${key_file}" ]]; then
  if [[ -z "${cert_file}" || -z "${key_file}" ]]; then
    echo "TLS_CERT_FILE and TLS_KEY_FILE must both be set" >&2
    exit 1
  fi
  if [[ ! -f "${cert_file}" ]]; then
    echo "TLS cert file not found: ${cert_file}" >&2
    exit 1
  fi
  if [[ ! -f "${key_file}" ]]; then
    echo "TLS key file not found: ${key_file}" >&2
    exit 1
  fi
  args+=(
    --ssl-certfile
    "${cert_file}"
    --ssl-keyfile
    "${key_file}"
  )
fi

exec "${args[@]}"
