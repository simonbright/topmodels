#!/bin/sh
# Install repo git hooks (no git config changes — copies into .git/hooks/).
set -e
root=$(cd "$(dirname "$0")/.." && pwd)
hooks_dir="$root/.git/hooks"
mkdir -p "$hooks_dir"
cp "$root/scripts/post-commit" "$hooks_dir/post-commit"
chmod +x "$hooks_dir/post-commit"
echo "Installed post-commit hook → $hooks_dir/post-commit (auto git push after commit)"
