#!/bin/sh
set -eu
test "$(cat /root/receiver-check.txt 2>/dev/null || true)" = "receiver works"
