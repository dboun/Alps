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
home-network device can't reach the Tailscale address.

Network access always uses HTTPS; HTTP is for this computer only. HTTPS uses Caddy
on **443**, with a private certificate. No domain needed. If Caddy is missing,
setup shows the install command. Setup prints your address and certificate
trust/copy commands. See [browser certificate setup](HTTPS.md).

With a Tailscale address you can also use the computer's Tailscale name with a
certificate every browser trusts. See [Tailscale and trusted certificates](HTTPS.md#tailscale-name-with-a-trusted-certificate).

If this computer's IP changes, rerun setup. Each address signs in separately.
If you expose local apps, also run `alps apply` to update their proxy addresses.

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
