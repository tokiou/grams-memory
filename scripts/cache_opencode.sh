#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)"
version="1.18.22"
cache_dir="$repo_root/.cache/opencode/$version/linux-x64"
binary="$cache_dir/opencode"
archive_url="https://github.com/anomalyco/opencode/releases/download/v$version/opencode-linux-x64.tar.gz"

mkdir -p "$cache_dir"

if [[ -x "$binary" ]]; then
  exit 0
fi

tmp_dir="$(mktemp -d "${TMPDIR:-/tmp}/grams-opencode.XXXXXX")"
cleanup() {
  rm -rf "$tmp_dir"
}
trap cleanup EXIT

archive="$tmp_dir/opencode.tar.gz"
downloaded=0
for delay in 0 2 5; do
  if (( delay > 0 )); then
    sleep "$delay"
  fi
  if curl --fail --silent --show-error --location --retry 1 --retry-delay 1 \
      --output "$archive" "$archive_url"; then
    downloaded=1
    break
  fi
done

if [[ "$downloaded" != "1" ]]; then
  printf 'GRAMS_OPENCODE_CACHE_DOWNLOAD_FAILED version=%s url=%s\n' "$version" "$archive_url" >&2
  exit 1
fi

tar -xzf "$archive" -C "$tmp_dir"
if [[ ! -x "$tmp_dir/opencode" ]]; then
  printf 'GRAMS_OPENCODE_CACHE_INVALID version=%s\n' "$version" >&2
  exit 1
fi
install -m 0755 "$tmp_dir/opencode" "$binary"
