#!/usr/bin/env bash
set -Eeuo pipefail
source_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
prefix="$HOME/.local/share/holdnote"
python3 -c 'import gi, Xlib, keyring, cryptography, requests'
install -d -m 700 "$prefix"
python3 -m venv --system-site-packages "$prefix/venv"
"$prefix/venv/bin/python" -m pip install --no-deps --no-build-isolation "$source_dir"
install -d -m 755 "$HOME/.local/bin" "$HOME/.config/systemd/user" "$HOME/.config/autostart"
if [[ -e "$HOME/.local/bin/holdnote" && ! -L "$HOME/.local/bin/holdnote" ]]; then
  printf 'Refusing to replace an existing non-symlink holdnote command.\n' >&2
  exit 1
fi
ln -sfn "$prefix/venv/bin/holdnote" "$HOME/.local/bin/holdnote"
install -m 644 "$source_dir/packaging/holdnote.service" "$HOME/.config/systemd/user/holdnote.service"
"$prefix/venv/bin/python" - <<'PY'
from pathlib import Path
p=Path.home()/'.config/autostart/holdnote.desktop'
command=Path.home()/'.local/share/holdnote/venv/bin/holdnote'
p.write_text('[Desktop Entry]\nType=Application\nName=HoldNote\nComment=Private text capture after login\nExec="'+str(command)+'" session-start\nOnlyShowIn=GNOME;\nX-GNOME-Autostart-enabled=true\nStartupNotify=false\n')
p.chmod(0o644)
PY
systemctl --user daemon-reload
systemctl --user enable holdnote.service
printf 'Installed. Run holdnote configure --url https://YOUR-MEMOS-HOST, then holdnote session-start.\n'
