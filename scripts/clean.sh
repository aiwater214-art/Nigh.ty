#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SNAPSHOT_DIR="${SNAPSHOT_DIR:-data/snapshots}"
WORLD_DIR="${WORLD_DIR:-data/worlds}"
DB_FILES=("app.db" "test.db")

remove_snapshots() {
  local target="$PROJECT_ROOT/$SNAPSHOT_DIR"
  if [ -d "$target" ]; then
    find "$target" -type f -name '*.json' -delete
    echo "Removed snapshot files from $target"
  else
    echo "Snapshot directory $target not found"
  fi
}

remove_worlds_dir() {
  local target="$PROJECT_ROOT/$WORLD_DIR"
  if [ -d "$target" ]; then
    rm -rf "$target"
    mkdir -p "$target"
    echo "Reset $target"
  else
    echo "World directory $target not found"
  fi
}

remove_databases() {
  local removed=false
  for db in "${DB_FILES[@]}"; do
    local path="$PROJECT_ROOT/$db"
    if [ -f "$path" ]; then
      rm -f "$path"
      echo "Deleted $path"
      removed=true
    fi
  done
  if ! $removed; then
    echo "No database files found"
  fi
}

remove_pycache() {
  find "$PROJECT_ROOT" -type d -name '__pycache__' -prune -exec rm -rf {} +
  echo "Purged __pycache__ directories"
}

full_cleanup() {
  remove_snapshots
  remove_worlds_dir
  remove_databases
  remove_pycache
}

cat <<MENU
Cleanup helper
--------------
1) Remove snapshot JSON files
2) Remove world runtime directory
3) Remove SQLite databases (app.db/test.db)
4) Purge __pycache__ folders
5) Run all cleanup steps
MENU

read -rp "Select option: " choice
case "$choice" in
  1) remove_snapshots ;;
  2) remove_worlds_dir ;;
  3) remove_databases ;;
  4) remove_pycache ;;
  5) full_cleanup ;;
  *) echo "Unknown option" >&2; exit 1 ;;
esac
