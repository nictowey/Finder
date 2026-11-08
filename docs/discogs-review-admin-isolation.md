# Caddy admin isolation before private review deployment

Prepared operational alternative, not a deployment or a passed live test. Scope:
Ubuntu 24, Caddy 2.6.2, systemd 255 built without BPF_FRAMEWORK. No socket was
opened or attempted while preparing this document. The repository environment's
prior socket denial is not authorization to test by another route.

## Boundary and remaining gates

Do not rely on `IPAddressDeny=localhost` or `IPAddressAllow=127.0.0.53/32` on this
host. The review service must remain stopped until an independently verified
replacement boundary is installed. This change isolates **Caddy admin only**.
It does not block review-UID access to ntfy at 127.0.0.1:2586, other local TCP
services, or Internet egress. Their authorization and confidentiality require
separate review. It does not solve the shared database/secret isolation gates in
[the deployment runbook](discogs-phone-review-deployment.md).

The proposed admin address is `unix//run/caddy-admin/admin.sock`. A 0700 directory
owned by Caddy blocks traversal by the review UID independently of cgroup BPF.
The review UID must be distinct from Caddy, have no Caddy group membership, no
ACL grant, no DAC-bypass capability and no privilege to execute as Caddy/root.
Do not use an abstract Unix socket. Do not proxy admin through any public site.

This is **root-operated**, not literally root-exclusive: Caddy necessarily owns
and can use its admin socket, and a process compromised as Caddy can control it.
Only root operators should initiate administration; grant the review account no
sudo/polkit/service-management authority. No admin password, token, or new
persistent credential is introduced. Keep the page authentication hash, Caddy
configuration and autosaved JSON private under their existing protections.

## Exact candidate configuration

Merge this line into the existing, single global Caddyfile options block (first
block), replacing any existing admin declaration. Do not add a second block:

```caddyfile
admin unix//run/caddy-admin/admin.sock
```

Use [the systemd drop-in](../deploy/phone-review/caddy-admin-isolation.conf).
It clears the existing ExecReload list and targets the Unix address explicitly.
Do not change User/Group, ExecStart, TLS storage, ntfy site content, or global
server timeouts as part of this isolation change. No `UMask` change is needed:
the private parent directory is the boundary even if the socket mode is broader.
The syntax and filesystem requirement are documented in
[Caddy's admin options](https://caddyserver.com/docs/caddyfile/options#admin).

The template assumes `/usr/bin/caddy`, `/etc/caddy/Caddyfile`, `User=caddy`, and
`Group=caddy`. Verify all four before use. RuntimeDirectory entries accumulate;
RuntimeDirectoryMode/Preserve affect the unit's other runtime directories too.
If existing runtime directories need different permissions/lifetimes, stop and
review a compatible unit rather than blindly applying this template. Inspect
mount-namespace restrictions and existing drop-ins for conflicts. RuntimeDirectory
is created for service execution, not merely by daemon-reload, and is removed on
stop with Preserve=no. See the
[systemd 255 execution manual source](https://github.com/systemd/systemd/blob/v255/man/systemd.exec.xml).

## Operator sequence (authorized live session only)

This is a manual checklist, not an unattended installer. Use one operator and
exclude concurrent deployments, timers or management tools that reload Caddy.
Run commands in a private root terminal with tracing off. Keep configuration,
logs, backups and adapted JSON out of public transcripts.

1. Confirm the page service is stopped and cannot be started by automation during
   maintenance. Do not expose 8443 yet. Inventory Caddy's active unit and drop-ins,
   exact ExecStart/config source (including `--resume`), service UID/GID, current
   listener ownership, dependencies, config-management jobs and reload callers.
   Stop if they differ from the assumptions. A file-based restart must load the
   reviewed file, not an old autosave. Record ntfy's existing health privately.
2. Back up the full config, referenced files and relevant unit/drop-ins into a
   root-only directory (0700; files 0600). Retain ownership/mode metadata. Prepare
   a candidate that changes only admin, preserving the ntfy site. Separately keep
   a known-good **Unix-admin, ntfy-only** config for later page rollback.
3. Adapt with the installed 2.6.2 binary into a root-only file; verify JSON
   `admin.listen` equals the proposed address and no remote admin is enabled.
   Validate as Caddy using its actual environment and private readable candidate.
   Validation does not test binding or the eventual Unix socket permissions.
   Do not print the adapted document (it can contain authentication hashes).
4. Review/install the drop-in root-owned, validate the combined unit with
   `systemd-analyze verify caddy.service`, then `systemctl daemon-reload`.
   Inspect effective ExecReload/RuntimeDirectory/User/Group. Atomically install
   the validated candidate at the actual ExecStart config path, with the same
   protected ownership and permissions as the original. Keep runtime and on-disk
   configuration synchronized, including referenced files.
5. Choose ONE transition below. Prefer a controlled restart if already required
   to refresh Caddy's supplementary proxy group membership. A restart interrupts
   ntfy connections; arrange the maintenance window before doing it.

### A. Controlled restart

With the page stopped and installed config/drop-in verified:

```sh
systemctl restart caddy.service
```

Systemd creates the private runtime directory before starting Caddy. If startup
fails, do not open a TCP admin fallback. Check the failure privately and restore
the known-good configuration as described below. Verify all gates before starting
the review service. Daemon-reload or a Caddy configuration reload alone does not
refresh the running Caddy process's supplementary groups.

### B. One-time TCP-to-Unix configuration reload

Use only when a restart is not needed and live Caddy identity/group access already
meets requirements. Before creation, verify `/run/caddy-admin` is absent or a real
Caddy-owned directory with no unexpected content, symlink or ACL. If absent,
create it as root with `install -d -o caddy -g caddy -m 0700 /run/caddy-admin`.
Verify its ownership, mode and lack of ACL grants before proceeding. Check that
Caddy can see/write that directory inside its actual mount namespace. Do not
chown an unexpected existing tree or pre-create the socket with `touch`.

The running endpoint is still 127.0.0.1:2019, so the initial command must explicitly
target that OLD endpoint, even though the candidate contains the NEW endpoint:

```sh
/usr/bin/caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile --address 127.0.0.1:2019 --force
```

Do not use `systemctl reload` for this initial transition: its new ExecReload
already targets Unix. Subsequent normal reloads use `systemctl reload caddy`.
The [2.6.2 reload implementation](https://github.com/caddyserver/caddy/blob/v2.6.2/cmd/commandfuncs.go)
prioritizes explicit `--address`; otherwise it derives the address from the new
configuration. It also supports Unix transport with an empty Host header.

## Reconcile results, then verify

A successful reload is not proof that the old TCP endpoint has disappeared.
Caddy shuts the prior admin server down asynchronously. A bind failure leaves the
old admin running, but a later app failure may occur AFTER admin has moved. See
[2.6.2 admin replacement](https://github.com/caddyserver/caddy/blob/v2.6.2/admin.go)
and [2.6.2 configuration startup ordering](https://github.com/caddyserver/caddy/blob/v2.6.2/caddy.go).
On timeout/error, inspect actual listeners and service state before any retry;
do not assume rollback, and do not submit another config blindly.

Required live evidence, collected privately by the authorized operator:

- Caddy active; `/run/caddy-admin` real directory, caddy-owned, 0700, no access ACL;
  socket exists and belongs to Caddy. Check with `namei`, `stat`, `getfacl`, and
  `ss -lxnp`. Review UID cannot traverse the directory or connect to the socket.
  A negative HTTP response is not proof of socket denial: expect filesystem
  permission denial, tested under the actual page UID/groups and sandbox.
- Root can read ONLY the non-secret admin listen field, returning the exact Unix
  address. For Caddy 2.6 use an empty Host header; for example:

  ```sh
  curl --http1.0 --noproxy '*' --unix-socket /run/caddy-admin/admin.sock --max-time 5 --fail --silent --show-error -H 'Host:' http://localhost/config/admin/listen
  ```

  HTTP/1.0 is required here when omitting Host; HTTP/1.1 without Host returns 400 on the verified host. Do not retrieve/print `/config/` or a full config. If the host curl rejects this
  form, investigate privately; do not relax Caddy origins or expose TCP.
- Both IPv4 and IPv6 TCP listener inventories show no Caddy admin endpoint at
  2019 or any other TCP port. Wait for bounded graceful shutdown and fail closed
  if it persists. Confirm no remote admin listener or public route reaches admin.
- A second normal systemd reload succeeds through Unix; Caddy PID stays stable;
  ntfy TLS/login/subscribe behavior is unchanged, without publishing a test unless
  separately authorized. Existing :443 routes/settings remain intact.
- Controlled restart and reboot persistence remain required gates if not yet
  exercised. Verify runtime directory recreation, Unix-only admin and existing
  ntfy service after each authorized lifecycle test. Never label unrun tests passed.
- Verify all other private-page gates before allowing its service or 8443 ingress.

## Rollback and recovery

Prefer rolling back page routing while RETAINING Unix admin and its drop-in.
Load the validated Unix-admin, ntfy-only config over the currently verified Unix
endpoint. Restore the on-disk source too so restart cannot resurrect page routes.

If isolation itself must be reverted, first stop/prevent restart of the page and
withdraw its route/ingress. Restore the backed-up original Caddyfile and only this
new drop-in's previous state (do not delete unrelated overrides), daemon-reload,
then perform a controlled Caddy restart after validation. This deliberately
restores the old localhost admin exposure, so the page MUST remain stopped. Check
ntfy recovery and record the isolation gate as failed, not completed.

If avoiding restart during rollback, send the original configuration explicitly
to whichever admin endpoint is actually running, then reconcile both listener
inventories again. Do not infer that endpoint from the restored file. If neither
endpoint is usable, use the validated on-disk restart recovery path.

Never remove/unlink a live admin socket. If a stale socket obstructs recovery,
first stop Caddy, verify no process owns/listens on it, and inspect the exact path
before any approved cleanup. RuntimeDirectory lifecycle normally removes it.
Do not introduce an always-on TCP fallback, global permissive chmod, Caddy-group
access for the page, or a helper running page operations as root.

## Verification record

Only source review and static artifact checks were performed for this alternative.
No Caddy binary is available in the preparation environment. No service, network,
firewall, permission, database, credential or live configuration change was made.
All host binding, denial, reload, restart, reboot and ntfy checks above are pending.
