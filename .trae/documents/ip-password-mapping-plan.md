# IP-Password Mapping Table for JumpServer Collection

## Context

Currently JumpServer collection uses a single `folidev` password configured in Settings, and `Terminal.elevate()` tries `sudo -i` only once. 57 servers have the wrong folidev sudo password, so they fail elevation and fall back to non-root collection every time — wasting 45s per server on a doomed sudo attempt and never trying alternative root passwords.

The goal: build a persistent **IP → (method, password, status)** mapping table. On each collection, use the mapped method directly. For unmapped IPs, probe three methods in order (`sudo -i` with folidev password → `su` with root alt password 1 → `su` with root alt password 2). If all three fail, mark `password_error` and skip elevation on subsequent runs. This self-bootstraps on the first collection after deployment.

## Files to Modify

### 1. `environment-web/runtime/server_inventory.py` — Terminal + elevation logic

**Add** **`elevate_su(root_password)`** **method to** **`Terminal`** **(after** **`elevate()`, \~L800):**

* Runs `su -` (starts root login shell), waits for password prompt, sends root password

* On wrong password: Ctrl+C, return `('password_error', 'su 密码错误')`

* On root shell: verify `id -u == 0`, return `('password', '')`

* On timeout: return `('timeout', 'su 超时')`

* Pattern mirrors existing `elevate()` — token markers, `clean()` buffer, deadline loop

**Add** **`resolve_elevation(terminal, ip, folidev_pw, root_alt1, root_alt2, ip_map)`** **function:**
Returns `(sudo_status, sudo_reason, method, password, map_updated)`.

Logic:

1. IP in map with `status=password_error` → return `('password_error', '映射表标记密码错误', None, None, False)` — skip all attempts
2. IP in map with `status=normal` → try mapped method+password via `elevate()` or `elevate_su()`. If success → return with `map_updated=False`. If fail → fall through to probe.
3. Probe (unmapped or mapped-failed):

   * `sudo -i` with `folidev_pw` (skip if not configured)

   * `su` with `root_alt1` (skip if not configured)

   * `su` with `root_alt2` (skip if not configured)

   * First success → return `(status, reason, method, password, True)` — map needs update

   * All fail → return `('password_error', '三种方式均无法登录', None, None, True)`

**Modify** **`inspect_asset()`** **(\~L980):**

* New signature: `inspect_asset(asset, accounts, cookie, folidev_pw, root_alt1, root_alt2, ip_map, terminal_factory=Terminal)`

* After `terminal.login()`, call `resolve_elevation()` instead of `terminal.elevate()`

* Rest of collection logic unchanged (uses `elevated` flag the same way)

* If `sudo_status == 'password_error'` from map → `elevated=False`, proceed with non-root collection, add warning "映射表标记密码错误，使用无 root 权限采集"

* Returns `elevationInfo` in result: `{method, mapUpdated}` so caller can update the map

**Modify** **`collect_servers()`** **(\~L1053):**

* New signature: `collect_servers(assets, get, cookie, folidev_pw, root_alt1, root_alt2, ip_map, progress=None)`

* Passes new args to `inspect_asset()`

* Returns `(results, ip_map_updates)` — collects map updates from each inspection

### 2. `environment-web/runtime/collect.py` — collection entry points

**Modify** **`jumpserver()`** **(\~L94):**

* New signature: `jumpserver(cookie, folder, folidev_pw, root_alt1, root_alt2, ip_map, fetcher=None)`

* Calls `collect_servers(all_assets, get, cookie, folidev_pw, root_alt1, root_alt2, ip_map, report_progress)`

* After collection, writes updated map to `folder / 'ip-password-map-updated.json'`

**Modify** **`jumpserver_asset()`** **(\~L184):**

* Same new args, single-asset path

* Also writes updated map to `folder / 'ip-password-map-updated.json'`

**Modify** **`main()`** **(\~L316):**

* Read `rootAlt1`, `rootAlt2`, `_ipPasswordMap` from credentials JSON

* Pass to `jumpserver()` / `jumpserver_asset()`

### 3. `environment-web/runtime/service.py` — settings + persistence

**Modify** **`save_settings()`** **(\~L340):**

* Accept `rootPasswordAlt1`, `rootPasswordAlt2` from payload

* Same validation as `folidevPassword` (non-empty, no newlines, length limit)

* Store as `saved['rootAlt1']` and `saved['rootAlt2']` in cookies.json

* **Auto-clear** **`password_error`** **entries** when any of the 3 passwords (folidev/rootAlt1/rootAlt2) is updated in the payload: read `DATA/secrets/ip-password-map.json`, remove entries with `status=password_error`, write back. This ensures next collection re-probes those IPs with the new passwords.

**Modify** **`status()`** **(\~L332):**

* Add `rootAlt1` and `rootAlt2` to response: `{configured, updatedAt}` (like `folidev`)

**Modify** **`work()`** **(\~L415):**

* Before collection: include current ip-password-map in temp credentials: `creds_data['_ipPasswordMap'] = read(DATA/'secrets/ip-password-map.json', {})`

* After successful collection: read `folder / 'ip-password-map-updated.json'`, merge into `DATA/secrets/ip-password-map.json`

**Add helper** **`password_map()`:**

* `read(DATA/'secrets/ip-password-map.json', {})` — used by `work()` to populate temp credentials

### 4. `environment-web/app/settings/page.tsx` — frontend

* Add state: `rootPasswordAlt1`, `rootPasswordAlt2`

* Add 2 input fields below folidev password field (\~L246):

  * "备选 root 密码 1" — for `su` login, used when folidev sudo fails

  * "备选 root 密码 2" — second alternative

* Include both in `post('config', {...})` calls

* Clear after save (same as folidevPassword)

* Show configured status from `state.rootAlt1` / `state.rootAlt2`

* **No map display in frontend** — map is backend-only, per user decision

### 5. Tests — `environment-web/runtime/test_server_inventory.py`

* Test `elevate_su` success (password accepted, root shell)

* Test `elevate_su` wrong password (returns password\_error)

* Test `resolve_elevation` with mapped IP (sudo-i normal → uses mapped method)

* Test `resolve_elevation` with password\_error IP (skips probing)

* Test `resolve_elevation` with unmapped IP (probes sudo-i → su alt1 → su alt2)

* Test `resolve_elevation` all-fail (returns password\_error, map\_updated=True)

## Data Structures

### IP Password Map (`DATA/secrets/ip-password-map.json`):

```json
{
  "10.58.1.100": {
    "method": "sudo-i",
    "password": "<folidev password>",
    "status": "normal",
    "verifiedAt": "2026-10-12T10:00:00+08:00"
  },
  "10.58.5.44": {
    "method": "su",
    "password": "<root alt password 1>",
    "status": "normal",
    "verifiedAt": "2026-10-12T10:00:00+08:00"
  },
  "10.58.9.50": {
    "method": null,
    "password": null,
    "status": "password_error",
    "verifiedAt": "2026-10-12T10:00:00+08:00"
  }
}
```

### Credentials file (temp, passed to collect.py):

Already has `folidev`, cookies. Added fields:

* `rootAlt1`: `{value, updatedAt}`

* `rootAlt2`: `{value, updatedAt}`

* `_ipPasswordMap`: current map (read-only input to collect.py)

## Collection Flow (after changes)

```
service.py work()
  → credentials() includes folidev, rootAlt1, rootAlt2
  → read ip-password-map.json, include as _ipPasswordMap in temp creds
  → collect.py main() reads all from temp creds
  → jumpserver() passes folidev_pw, root_alt1, root_alt2, ip_map to collect_servers()
  → collect_servers() → inspect_asset() per IP:
      1. Look up IP in ip_map
      2. resolve_elevation() → try mapped method OR probe all 3
      3. Collect processes/ports/nginx/webapps/specs (elevated or non-root)
      4. Record elevation result for map update
  → collect_servers() returns (inspections, map_updates)
  → jumpserver() writes map_updates to folder/ip-password-map-updated.json
  → service.py reads it, merges into DATA/secrets/ip-password-map.json
```

## Deployment

1. Edit `server_inventory.py`, `collect.py`, `service.py`, `app/settings/page.tsx`
2. Run tests: `cd environment-web/runtime && python3 test_server_inventory.py`
3. `cd environment-web && bash build.sh` (builds image, syncs collectors)
4. `docker --context lenovo compose up -d` (restart container)
5. Settings page: configure folidev password + 2 root alt passwords, save
6. Trigger JumpServer sync — first run probes all IPs and builds the map
7. Subsequent runs use the map directly (fast), only re-probing new/failed IPs

## Verification

* After first collection, check `DATA/secrets/ip-password-map.json` on the container: should have entries for all collected IPs with correct method/status

* The 57 previously-failed IPs should now show `method=su, status=normal` (if an alt password worked) or `status=password_error` (if none worked)

* Second collection should be faster — no probing for mapped IPs, password\_error IPs skip elevation entirely

* Settings page should show all 3 passwords as "已配置" after saving

