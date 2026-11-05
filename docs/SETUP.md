# Setup and maintenance

Run `./install.sh` to install or update Alps. Rerunning setup offers your previous
answers as defaults. Apps stay saved, and existing Caddy sites are kept.

Choose a username, password, and who can connect. Setup prints the address to open
and any remaining setup commands. Open that address and sign in, then choose
**Add app** to save a name and web address. See [Using Alps](USAGE.md) for more.

## Who can connect

- `local`: this computer only.
- `lan`: your home network.
- `lan+ips`: your home network and chosen addresses, such as Tailscale.
- `ips`: only chosen addresses, such as Tailscale. Devices on your home network can't connect.

Chosen addresses come from this computer's own interfaces. A firewall rule
(nftables) makes each one accept traffic only from its own network, so a
home-network device can't reach the Tailscale address. Alps checks the rule about every 30
seconds and restores it if something flushed it. If it can't, it stops Caddy, so Alps goes offline
instead of running without the rule. Rerun setup to bring it back. Setup can make the same check
restore `ufw`, which a flush usually wipes too. A flush can also remove Tailscale's own rules, so
run `sudo systemctl restart tailscaled` after one.

On a laptop, choose `ips`. Caddy can't start while a chosen address is missing, so with
`lan+ips` a reboot away from home also takes down Tailscale access. With `ips`, the same
happens if Tailscale is off when Caddy starts. While Tailscale is off, tiles don't work, but
`http://127.0.0.1:PORT` still opens each app on this computer.

Alps only controls its own listeners. On a laptop, also block other incoming traffic, such as
apps that listen on every network:

```sh
sudo ufw default deny incoming
sudo ufw allow in on tailscale0 to any port 443 proto tcp
sudo ufw allow in on tailscale0 to any port 18443:19443 proto tcp
sudo ufw enable
```

An app exposed with its own `--port` outside that range needs its own rule.

Setup offers to remove Caddy's default welcome page, because it listens on port 80 on every
network, and it warns about any other Caddy site without a fixed address. Caddy's admin API has
no password, so setup keeps it on `/run/caddy/admin.sock` instead of a TCP port, unless your
Caddyfile already sets an admin address. When an address stops being served, setup offers to sign
out every device.

Network access always uses HTTPS; HTTP is for this computer only. HTTPS uses Caddy
on **443**, with a private certificate. No domain needed. If Caddy is missing,
setup shows the install command. Setup prints your address and certificate
trust/copy commands. See [browser certificate setup](HTTPS.md).

With a Tailscale address you can also use the computer's Tailscale name with a
certificate every browser trusts. See [Tailscale and trusted certificates](HTTPS.md#tailscale-name-with-a-trusted-certificate).

If this computer's IP changes, rerun setup and choose `change`. It also updates the proxy addresses of exposed
apps. Each address signs in separately.

## Settings and backups

Settings live in `config.json`; apps and copied icons live in `data/`. Keep both.

## Stop Alps

```sh
./install.sh --stop
```

## Logs

```sh
journalctl --user -u alps.service -n 30
```

## Exposed apps after an update

After updating Alps, run `alps apply` so exposed apps get the latest HTTPS routes
and security fixes. This does not change certificates. See [CLI details](CLI.md).
