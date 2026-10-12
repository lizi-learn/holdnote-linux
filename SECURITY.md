# Security and privacy boundaries

Do not commit or attach PATs, cookies, passwords, Telegram sessions, selected text,
outbox databases, production data volumes or Secret Service exports to issues/PRs.
Use GitHub's private vulnerability reporting instead of posting a live credential.

The default destination is only the configured Memos instance. TLS is verified,
redirects are refused, notes are PRIVATE, and normal-user tokens are recommended.
Memos owners/admins can still read server-side notes: this is not E2EE.

Desktop credentials and the Fernet outbox key use GNOME Secret Service, not a
plaintext fallback keyring. Offline text is encrypted before it reaches SQLite.
Do not delete/reset the key until pending notes have been recovered.

X11 is inherently permissive: desktop applications can observe input and the
PRIMARY selection. HoldNote does not provide a sandbox against other X11 apps.
It discards ordinary key event metadata without converting it into text; it reads
selected text only for the user's explicit capture workflow. Do not use an
untrusted fork on a sensitive workstation.

The service changes two GNOME gesture settings while active and restores them on
stop. It checks current values before restoring, to preserve later manual edits.
If desktop shutdown prevents restoration, run `holdnote restore-settings` next
login. Wayland and non-GNOME desktops are explicitly unsupported in v0.1.

No Telegram/WeChat targets or screenshot capture are enabled in this release.
