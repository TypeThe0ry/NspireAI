#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DEPS="$ROOT/.deps"
mkdir -p "$DEPS"

clone_at() {
  local name="$1" url="$2" rev="$3"
  if [ ! -d "$DEPS/$name/.git" ]; then
    git clone "$url" "$DEPS/$name"
  fi
  git -C "$DEPS/$name" fetch --all --tags --prune
  git -C "$DEPS/$name" checkout --detach "$rev"
  printf '%-14s %s\n' "$name" "$(git -C "$DEPS/$name" rev-parse HEAD)"
}

clone_at ndless https://github.com/ndless-nspire/Ndless.git 9484d8da7c7a4dde9766138c2e42e1d1e3acfcd4
clone_at n-link https://github.com/lights0123/n-link.git 0472908ef4961e7eec92cc0b97376420a71e5bc7
clone_at libnspire-rs https://github.com/lights0123/libnspire-rs.git 098b3f5fdc09a5b5b0d97688672c10d34365786c
clone_at luna https://github.com/ndless-nspire/Luna.git a9924a9a968954eba9adcc161a58ac607f97ce8c

echo "Upstreams are ready in $DEPS"
