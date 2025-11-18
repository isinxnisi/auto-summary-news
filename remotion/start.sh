#!/usr/bin/env bash
set -euo pipefail

APP_ROOT=/app
cd "$APP_ROOT"

# pick working app directory
pick_app_dir() {
  local candidate=""
  # If explicit env is set and valid, use it
  if [ -n "${REMOTION_APP_DIR:-}" ] && [ -f "$APP_ROOT/${REMOTION_APP_DIR}/package.json" ]; then
    echo "$APP_ROOT/${REMOTION_APP_DIR}"
    return 0
  fi
  # If current dir has package.json, use it
  if [ -f "$APP_ROOT/package.json" ]; then
    local has_script
    if node -e "p=require('$APP_ROOT/package.json');process.exit(p.scripts&&(p.scripts.dev||p.scripts.start)?0:1)"; then
      echo "$APP_ROOT"
      return 0
    fi
    # else, keep looking for a proper subproject
  fi
  # Search immediate subdirectories for a package.json
  local found=()
  while IFS= read -r -d '' d; do
    if [ -f "$d/package.json" ]; then
      found+=("$d")
    fi
  done < <(find "$APP_ROOT" -mindepth 1 -maxdepth 1 -type d -print0)

  if [ ${#found[@]} -eq 1 ]; then
    echo "${found[0]}"
    return 0
  fi
  # none or ambiguous
  echo ""
  return 1
}

APP_DIR="$(pick_app_dir || true)"

if [ -z "$APP_DIR" ]; then
  echo "[remotion] /app に package.json がありません。初回セットアップを試みます…"
  AUTO_INIT=${REMOTION_AUTO_INIT:-true}
  TEMPLATE=${REMOTION_TEMPLATE:-blank}
  LANGUAGE=${REMOTION_LANGUAGE:-ts}
  PKG_MGR=${REMOTION_PACKAGE_MANAGER:-npm}

  if [ "$AUTO_INIT" = "true" ]; then
    echo "[remotion] npx create-video を自動実行します (template=$TEMPLATE, lang=$LANGUAGE, pm=$PKG_MGR)."
    set +e
    # 試行1: 明示フラグ + CI モード
    CI=1 npx create-video@latest --yes --template "$TEMPLATE" --language "$LANGUAGE" --package-manager "$PKG_MGR" .
    status=$?
    if [ $status -ne 0 ] || [ ! -f package.json ]; then
      echo "[remotion] 再試行1: 別名フラグ (--lang/--pm)"
      CI=1 npx create-video@latest --yes --template "$TEMPLATE" --lang "$LANGUAGE" --pm "$PKG_MGR" .
      status=$?
    fi
    if [ $status -ne 0 ] || [ ! -f package.json ]; then
      echo "[remotion] 再試行2: デフォルト受入（Enter を自動送信）"
      printf '\n' | npx create-video@latest --yes .
      status=$?
    fi
    set -e

    if [ ! -f package.json ]; then
      echo "[remotion] create-video の自動初期化に失敗しました。手動実行を案内して待機します。"
      echo "[remotion] 手動例: npx create-video@latest --yes"
      exec tail -f /dev/null
    fi
  else
    echo "[remotion] REMOTION_AUTO_INIT=false のため自動初期化はスキップ。待機します。"
    echo "[remotion] 手動初期化の例: npx create-video@latest"
    exec tail -f /dev/null
  fi
fi

if [ -z "$APP_DIR" ]; then
  # pick again after possible init
  APP_DIR="$(pick_app_dir || true)"
fi

if [ -z "$APP_DIR" ]; then
  echo "[remotion] 有効な Remotion プロジェクトが見つかりません（package.json 不在）。待機します。"
  exec tail -f /dev/null
fi

cd "$APP_DIR"

# Install dependencies（初回 or node_modules 不在のときのみ）
if [ -d node_modules ] && [ -f package-lock.json ]; then
  echo "[remotion] node_modules が存在するため依存の再インストールをスキップします。"
else
  if [ -f package-lock.json ] || [ -f npm-shrinkwrap.json ]; then
    echo "[remotion] package-lock を検出。npm ci を実行します（初回）。"
    npm ci --no-audit --progress=false
  else
    echo "[remotion] package-lock が無いので npm install を実行します（初回）。"
    npm install --no-audit --progress=false
  fi
fi

# Choose dev/start script
if node -e "p=require('./package.json');process.exit(p.scripts&&p.scripts.dev?0:1)"; then
  echo "[remotion] アプリを起動します: npm run dev"
  exec npm run dev
elif node -e "p=require('./package.json');process.exit(p.scripts&&p.scripts.start?0:1)"; then
  echo "[remotion] アプリを起動します: npm run start"
  exec npm run start
else
  echo "[remotion] package.json に dev/start スクリプトが見つかりません。npm run で確認してください。待機します。"
  exec tail -f /dev/null
fi
