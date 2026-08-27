#!/usr/bin/env bash
# =============================================================================
# backup_kb.sh — Backup / restore della Knowledge Base Sedna
#
# La KB vive in ~/.hermes/knowledge/sedna/. L'indice (indexes/) è "disposable
# by design": si ricostruisce dai manifest. Il backup "vero" è la conoscenza
# canonica (manifests/ + semantic_bundles/), non l'indice.
#
# USO:
#   backup_kb.sh backup [--full] [--dest DIR]   # backup timestamped
#   backup_kb.sh restore <backup_dir>            # ripristina da un backup
#   backup_kb.sh list                           # elenca i backup disponibili
#   backup_kb.sh verify [--dir DIR]             # verifica integrità KB live
#
# OPZIONI:
#   --full   copia TUTTO (incluso indexes/, engagements/, quarantine/).
#            Default: solo manifests/ + semantic_bundles/ (conoscenza canonica).
#   --dest   directory di destinazione (default: ~/sedna-kb-backups/)
#
# NOTE:
#   - Ogni backup è reversibile; non tocca mai la KB live.
#   - Dopo un restore, ricostruire l'indice con:
#       sedna_knowledge_maintenance(operation="rebuild")
#     oppure: ~/sedna/rebuild_index.py
#   - Il "source of truth" più portabile resta il repo dei writeup
#     (~/htb-writeups/write-ups/) + i bundle agentici
#     (~/sedna/scripts/agent-populate/bundles/*.json): da quelli la KB si
#     ricostruisce da zero con agent_relearn.py.
# =============================================================================
set -euo pipefail

KB_ROOT="${SEDNA_KB_ROOT:-$HOME/.hermes/knowledge/sedna}"
BACKUP_ROOT="${SEDNA_BACKUP_ROOT:-$HOME/sedna-kb-backups}"
TS="$(date +%Y%m%dT%H%M%SZ)"

# Componenti canonici (conoscenza) — sempre inclusi
CORE_DIRS=(manifests semantic_bundles)
# Componenti opzionali — inclusi solo con --full
FULL_DIRS=(indexes engagements quarantine semantic_quarantine transactions \
           promotion_publication_guards semantic_compilation_guards \
           semantic_verification)

die() { echo "ERROR: $*" >&2; exit 1; }

require_kb() {
  [ -d "$KB_ROOT" ] || die "KB root non trovato: $KB_ROOT (usa SEDNA_KB_ROOT per override)"
}

cmd_backup() {
  local full=0 dest="$BACKUP_ROOT"
  while [ $# -gt 0 ]; do
    case "$1" in
      --full) full=1 ;;
      --dest) dest="$2"; shift ;;
      *) die "argomento sconosciuto: $1" ;;
    esac
    shift
  done
  require_kb
  mkdir -p "$dest"
  local out="$dest/sedna-kb-backup-$TS"
  mkdir -p "$out"

  local d
  for d in "${CORE_DIRS[@]}"; do
    if [ -d "$KB_ROOT/$d" ]; then
      cp -rp "$KB_ROOT/$d" "$out/" && echo "  + $d/"
    else
      echo "  - $d/ (assente, saltato)"
    fi
  done

  if [ "$full" -eq 1 ]; then
    for d in "${FULL_DIRS[@]}"; do
      if [ -d "$KB_ROOT/$d" ]; then
        cp -rp "$KB_ROOT/$d" "$out/" && echo "  + $d/ (full)"
      else
        echo "  - $d/ (assente, saltato)"
      fi
    done
  fi

  # Manifest di backup (metadata)
  {
    echo "created_at: $TS"
    echo "kb_root: $KB_ROOT"
    echo "mode: $([ $full -eq 1 ] && echo full || echo core)"
    echo "core_dirs: ${CORE_DIRS[*]}"
    [ $full -eq 1 ] && echo "full_dirs: ${FULL_DIRS[*]}"
    echo "source_count: $(ls "$KB_ROOT/manifests" 2>/dev/null | wc -l)"
    echo "bundle_count: $(ls "$KB_ROOT/semantic_bundles" 2>/dev/null | wc -l)"
  } > "$out/BACKUP_MANIFEST.txt"

  echo
  echo "Backup completato: $out"
  echo "  source: $(ls "$out/manifests" 2>/dev/null | wc -l)  bundle: $(ls "$out/semantic_bundles" 2>/dev/null | wc -l)"
  echo
  echo "Per ripristinare:  backup_kb.sh restore $out"
  echo "Poi ricostruisci l'indice:  sedna_knowledge_maintenance(operation=\"rebuild\")"
}

cmd_restore() {
  [ $# -ge 1 ] || die "uso: backup_kb.sh restore <backup_dir>"
  local src="$1"
  [ -d "$src" ] || die "backup non trovato: $src"
  [ -d "$src/manifests" ] || die "backup non valido (manca manifests/): $src"
  mkdir -p "$KB_ROOT"

  # Backup di sicurezza della KB attuale prima di sovrascrivere
  local pre="$BACKUP_ROOT/sedna-kb-pre-restore-$TS"
  mkdir -p "$pre"
  cp -rp "$KB_ROOT/manifests" "$KB_ROOT/semantic_bundles" "$pre/" 2>/dev/null || true
  echo "Backup pre-restore della KB attuale: $pre"

  local d
  for d in "${CORE_DIRS[@]}"; do
    if [ -d "$src/$d" ]; then
      rm -rf "$KB_ROOT/$d"
      cp -rp "$src/$d" "$KB_ROOT/"
      echo "  ripristinato $d/"
    fi
  done

  echo
  echo "Restore completato da: $src"
  echo "IMPORTANTE: ricostruisci l'indice ora:"
  echo "  sedna_knowledge_maintenance(operation=\"rebuild\")"
  echo "  oppure: ~/sedna/rebuild_index.py"
  echo "Poi verifica: sedna_knowledge_maintenance(operation=\"audit\")"
}

cmd_list() {
  mkdir -p "$BACKUP_ROOT"
  echo "Backup in $BACKUP_ROOT:"
  local found=0
  for b in "$BACKUP_ROOT"/sedna-kb-backup-*; do
    [ -e "$b" ] || continue
    found=1
    local mode="core"
    [ -d "$b/indexes" ] && mode="full"
    echo "  $(basename "$b")  [$mode]  source=$(ls "$b/manifests" 2>/dev/null | wc -l) bundle=$(ls "$b/semantic_bundles" 2>/dev/null | wc -l)"
  done
  if [ "$found" -eq 0 ]; then
    echo "  (nessun backup)"
  fi
}

cmd_verify() {
  local dir="$KB_ROOT"
  if [ "${1:-}" = "--dir" ]; then
    dir="${2:-}"
    shift 2
  fi
  [ -d "$dir" ] || die "dir non trovata: $dir"
  echo "Verifica integrità KB: $dir"
  echo "  manifests:      $(ls "$dir/manifests" 2>/dev/null | wc -l)"
  echo "  semantic_bundles: $(ls "$dir/semantic_bundles" 2>/dev/null | wc -l)"
  echo "  indexes/:       $(ls "$dir/indexes" 2>/dev/null | wc -l) file"
  # Controllo marker di blocco
  if [ -f "$dir/indexes/.retrieval.sqlite.unavailable" ]; then
    echo "  ⚠️  marker .retrieval.sqlite.unavailable PRESENTE — indice bloccato"
  else
    echo "  ✓ nessun marker di blocco"
  fi
  echo
  echo "Per un audit completo usa il tool: sedna_knowledge_maintenance(operation=\"audit\")"
}

case "${1:-}" in
  backup)  shift; cmd_backup "$@" ;;
  restore) shift; cmd_restore "$@" ;;
  list)    shift; cmd_list "$@" ;;
  verify)  shift; cmd_verify "$@" ;;
  *) echo "USO: $0 {backup|restore|list|verify} [opzioni]"; exit 1 ;;
esac
