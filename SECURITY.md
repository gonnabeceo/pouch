# Security

Pouch is a local, single-user app. It never asks for private keys or seed phrases — only public addresses.

## How data is protected

- **Encrypted at rest.** The whole SQLite database is encrypted with SQLCipher 4. The 256-bit key is derived from your master password with Argon2id (3 passes, 64 MiB, 4 lanes). The password itself is never stored; `data/pouch.meta.json` holds only the salt and KDF parameters.
- **Key lives in memory only** while the app is unlocked. Locking — manually or after 15 minutes of inactivity — closes the database, and SQLCipher wipes the key. Python may keep transient copies until garbage collection, so full-disk encryption (FileVault on macOS) is strongly recommended.
- **Brute force:** after 5 wrong passwords, an exponential delay of up to 15 minutes kicks in (unlock, export and password change share it).
- **Local only.** The server binds to `127.0.0.1`. It checks `Host` and `Origin` to block DNS rebinding and cross-site requests; the session lives in an `HttpOnly`, `SameSite=Strict` cookie.
- **No third-party code in the browser.** A strict Content Security Policy allows only same-origin scripts, styles, fonts and requests. No telemetry, no CDNs.
- **Provider API keys** are stored inside the encrypted database and are never returned to the browser.
- **Logs** contain no addresses or amounts (the HTTP client's URL logging is silenced on purpose).
- **Exports** (CSV/JSON/backup) require re-entering the master password.

## Known limitations

- Data providers (mempool.space, TonAPI, Zerion, CoinGecko, Hyperliquid, …) see the addresses you query and your IP address. Full privacy requires running your own nodes, which is out of scope.
- If you forget the master password, the data cannot be recovered — by design. Keep encrypted backups.
- Anyone with access to your unlocked user session can open the app while it is unlocked.

## Reporting a vulnerability

Please use GitHub's **private vulnerability reporting** (the *Security* tab → *Report a vulnerability*) instead of opening a public issue.
