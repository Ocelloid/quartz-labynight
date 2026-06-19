#!/usr/bin/env bash
# Синхронизация Obsidian-vault LAbN-story → quartz-labynight
#
# Лор (content/) и картинки — публикуются Quartz.
# Сценарии (Cюжет/) — в private/scenarios/ (вне git).
# Книги (books/) — опционально, уже лежат в корне репозитория.
#
# Примеры:
#   ./scripts/sync-vault.sh --dry-run
#   ./scripts/sync-vault.sh --lore --images
#   ./scripts/sync-vault.sh --all
#   VAULT=/path/to/LAbN-story ./scripts/sync-vault.sh --all

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

VAULT="${VAULT:-/home/ocelloid/Windows/_vaults/RPGs/LAbN-story}"
CONTENT_DIR="$REPO_ROOT/content"
PRIVATE_SCENARIOS_DIR="$REPO_ROOT/private/scenarios"
PRIVATE_NOTES_DIR="$REPO_ROOT/private/notes"
BOOKS_DIR="$REPO_ROOT/books"

DRY_RUN=false
SYNC_LORE=false
SYNC_IMAGES=false
SYNC_SCENARIOS=false
SYNC_BOOKS=false
RENAME_SCENARIO_MAIN=true
VERBOSE=false

# Счётчики
STAT_ADDED=0
STAT_UPDATED=0
STAT_UNCHANGED=0
STAT_SKIPPED=0

usage() {
  cat <<'EOF'
sync-vault.sh — синхронизация LAbN-story → quartz-labynight

Использование:
  sync-vault.sh [опции]

Опции:
  --dry-run          Только отчёт, без записи файлов
  --all              Лор + картинки + сценарии (по умолчанию, если ничего не указано)
  --lore             content/: index.md, География/, Места и лица/, Фракции/
  --images           Картинки в content/ (co-located + корень для index.md)
  --scenarios        Cюжет/Ваншоты/ → private/scenarios/
  --books            books/ → books/ (без _pdf/)
  --no-rename-scenario-main
                     Не переименовывать <имя>/<имя>.md → сценарий.md
  --verbose          Показывать пропущенные (без изменений) файлы
  -h, --help         Справка

Переменные окружения:
  VAULT              Путь к vault (по умолчанию: .../LAbN-story)

Исключения:
  - content/templates/ не перезаписывается
  - Untitled.md, .obsidian/, *.psd
  - Удаление файлов в repo не выполняется (только add/update)
EOF
}

log() {
  printf '%s\n' "$*"
}

die() {
  printf 'Ошибка: %s\n' "$*" >&2
  exit 1
}

normalize_md_to() {
  local src="$1"
  local dst="$2"
  sed 's/\r$//' "$src" >"$dst"
}

file_hash() {
  sha256sum "$1" | awk '{print $1}'
}

md_normalized_hash() {
  local tmp
  tmp="$(mktemp)"
  normalize_md_to "$1" "$tmp"
  file_hash "$tmp"
  rm -f "$tmp"
}

ensure_parent_dir() {
  local path="$1"
  if [[ "$DRY_RUN" == true ]]; then
    return 0
  fi
  mkdir -p "$(dirname "$path")"
}

copy_binary() {
  local src="$1"
  local dst="$2"
  ensure_parent_dir "$dst"
  if [[ "$DRY_RUN" == true ]]; then
    return 0
  fi
  cp -f "$src" "$dst"
}

copy_md() {
  local src="$1"
  local dst="$2"
  ensure_parent_dir "$dst"
  if [[ "$DRY_RUN" == true ]]; then
    return 0
  fi
  normalize_md_to "$src" "$dst"
}

sync_file() {
  local src="$1"
  local dst="$2"
  local kind="${3:-auto}"

  if [[ ! -f "$src" ]]; then
    die "Источник не найден: $src"
  fi

  if [[ "$kind" == auto ]]; then
    case "${src##*.}" in
      md) kind=md ;;
      *) kind=binary ;;
    esac
  fi

  local action=added
  if [[ -f "$dst" ]]; then
    action=updated
    local same=false
    if [[ "$kind" == md ]]; then
      [[ "$(md_normalized_hash "$src")" == "$(md_normalized_hash "$dst")" ]] && same=true
    else
      [[ "$(file_hash "$src")" == "$(file_hash "$dst")" ]] && same=true
    fi
    if [[ "$same" == true ]]; then
      STAT_UNCHANGED=$((STAT_UNCHANGED + 1))
      if [[ "$VERBOSE" == true ]]; then
        log "  = без изменений: ${dst#"$REPO_ROOT"/}"
      fi
      return 0
    fi
  fi

  if [[ "$action" == added ]]; then
    STAT_ADDED=$((STAT_ADDED + 1))
    log "  + добавлен: ${dst#"$REPO_ROOT"/}"
  else
    STAT_UPDATED=$((STAT_UPDATED + 1))
    log "  ~ обновлён: ${dst#"$REPO_ROOT"/}"
  fi

  if [[ "$kind" == md ]]; then
    copy_md "$src" "$dst"
  else
    copy_binary "$src" "$dst"
  fi
}

should_skip_basename() {
  local base="$1"
  case "$base" in
    Untitled.md) return 0 ;;
    *.psd) return 0 ;;
  esac
  return 1
}

sync_tree() {
  local src_root="$1"
  local dst_root="$2"
  local label="$3"
  local only_md="${4:-false}"

  [[ -d "$src_root" ]] || die "Каталог vault не найден: $src_root"

  log ""
  log "== $label =="
  log "   $src_root"
  log " → ${dst_root#"$REPO_ROOT"/}"

  local find_args=("$src_root" -type f)
  if [[ "$only_md" == true ]]; then
    find_args+=(-name '*.md')
  fi

  while IFS= read -r -d '' src; do
    local rel="${src#"$src_root"/}"
    local base
    base="$(basename "$src")"

    if should_skip_basename "$base"; then
      STAT_SKIPPED=$((STAT_SKIPPED + 1))
      continue
    fi

    sync_file "$src" "$dst_root/$rel"
  done < <(find "${find_args[@]}" -print0)
}

sync_lore() {
  sync_file "$VAULT/index.md" "$CONTENT_DIR/index.md" md

  for dir in География "Места и лица" Фракции; do
    sync_tree "$VAULT/$dir" "$CONTENT_DIR/$dir" "Лор: $dir" true
  done
}

sync_root_images() {
  log ""
  log "== Картинки: корень content/ (для index.md) =="

  local img
  for img in char0.jpg char1.jpg domains.png guide0.png guide1.png; do
    if [[ -f "$VAULT/$img" ]]; then
      sync_file "$VAULT/$img" "$CONTENT_DIR/$img" binary
    fi
  done
}

sync_content_images() {
  log ""
  log "== Картинки: co-located в content/ =="

  local src
  while IFS= read -r -d '' src; do
    local rel="${src#"$VAULT"/}"
    # Только картинки внутри лор-деревьев
    case "$rel" in
      География/*|"Места и лица"/*|Фракции/*) ;;
      *) continue ;;
    esac
    sync_file "$src" "$CONTENT_DIR/$rel" binary
  done < <(
    find "$VAULT/География" "$VAULT/Места и лица" "$VAULT/Фракции" \
      -type f \( -iname '*.jpg' -o -iname '*.jpeg' -o -iname '*.png' -o -iname '*.webp' -o -iname '*.gif' \) \
      -print0 2>/dev/null
  )
}

sync_scenario_vault_file() {
  local src="$1"
  local scenario_name="$2"
  local rel_inside="${src#"$VAULT/Cюжет/Ваншоты/$scenario_name"/}"
  local base
  base="$(basename "$src")"
  local dst_rel="$rel_inside"
  local dst

  if should_skip_basename "$base"; then
    STAT_SKIPPED=$((STAT_SKIPPED + 1))
    return 0
  fi

  if [[ "$RENAME_SCENARIO_MAIN" == true && "$rel_inside" == "$scenario_name.md" ]]; then
    dst_rel="сценарий.md"
  fi

  dst="$PRIVATE_SCENARIOS_DIR/$scenario_name/$dst_rel"
  sync_file "$src" "$dst"
}

sync_scenarios() {
  local vanshots="$VAULT/Cюжет/Ваншоты"
  [[ -d "$vanshots" ]] || {
    log ""
    log "== Сценарии: каталог не найден, пропуск =="
    return 0
  }

  log ""
  log "== Сценарии: Cюжет/Ваншоты/ → private/scenarios/ =="

  local scenario_dir
  for scenario_dir in "$vanshots"/*; do
    [[ -d "$scenario_dir" ]] || continue
    local scenario_name
    scenario_name="$(basename "$scenario_dir")"
    [[ "$scenario_name" == "Сюжет.md" ]] && continue

    log ""
    log "  · $scenario_name"

    local src
    while IFS= read -r -d '' src; do
      sync_scenario_vault_file "$src" "$scenario_name"
    done < <(find "$scenario_dir" -type f -print0)
  done

  if [[ -f "$vanshots/Сюжет.md" ]]; then
    sync_file "$vanshots/Сюжет.md" "$PRIVATE_SCENARIOS_DIR/_index.md" md
  fi

  if [[ -d "$VAULT/Заметки" ]]; then
    sync_tree "$VAULT/Заметки" "$PRIVATE_NOTES_DIR" "Заметки → private/notes" true
  fi
}

sync_books() {
  [[ -d "$VAULT/books" ]] || die "Каталог books/ не найден в vault"

  log ""
  log "== Книги: books/ =="

  local src
  while IFS= read -r -d '' src; do
    local rel="${src#"$VAULT/books"/}"
    case "$rel" in
      _pdf/*) STAT_SKIPPED=$((STAT_SKIPPED + 1)); continue ;;
    esac
    sync_file "$src" "$BOOKS_DIR/$rel"
  done < <(find "$VAULT/books" -type f -print0)
}

print_summary() {
  log ""
  log "== Итог =="
  if [[ "$DRY_RUN" == true ]]; then
    log "Режим dry-run: файлы не записаны."
  fi
  log "  добавлено:     $STAT_ADDED"
  log "  обновлено:     $STAT_UPDATED"
  log "  без изменений: $STAT_UNCHANGED"
  log "  пропущено:     $STAT_SKIPPED"
}

parse_args() {
  local explicit_mode=false

  while [[ $# -gt 0 ]]; do
    case "$1" in
      --dry-run) DRY_RUN=true ;;
      --all)
        SYNC_LORE=true
        SYNC_IMAGES=true
        SYNC_SCENARIOS=true
        explicit_mode=true
        ;;
      --lore)
        SYNC_LORE=true
        explicit_mode=true
        ;;
      --images)
        SYNC_IMAGES=true
        explicit_mode=true
        ;;
      --scenarios)
        SYNC_SCENARIOS=true
        explicit_mode=true
        ;;
      --books)
        SYNC_BOOKS=true
        explicit_mode=true
        ;;
      --no-rename-scenario-main) RENAME_SCENARIO_MAIN=false ;;
      --verbose) VERBOSE=true ;;
      -h | --help)
        usage
        exit 0
        ;;
      *)
        die "Неизвестный аргумент: $1 (см. --help)"
        ;;
    esac
    shift
  done

  if [[ "$explicit_mode" == false ]]; then
    SYNC_LORE=true
    SYNC_IMAGES=true
    SYNC_SCENARIOS=true
  fi
}

main() {
  parse_args "$@"

  [[ -d "$VAULT" ]] || die "Vault не найден: $VAULT (задайте VAULT=...)"
  [[ -d "$CONTENT_DIR" ]] || die "Каталог content/ не найден: $CONTENT_DIR"

  log "Vault:  $VAULT"
  log "Repo:   $REPO_ROOT"
  if [[ "$DRY_RUN" == true ]]; then
    log "Режим:  dry-run"
  fi

  if [[ "$SYNC_LORE" == true ]]; then
    sync_lore
  fi

  if [[ "$SYNC_IMAGES" == true ]]; then
    sync_root_images
    sync_content_images
  fi

  if [[ "$SYNC_SCENARIOS" == true ]]; then
    sync_scenarios
  fi

  if [[ "$SYNC_BOOKS" == true ]]; then
    sync_books
  fi

  print_summary
}

main "$@"
