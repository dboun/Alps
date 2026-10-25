# Local apps through Alps

Keep the app running on `127.0.0.1` or `::1`. Then:

```sh
alps expose http://127.0.0.1:3000 --name Notebook
```

Alps prints the HTTPS addresses (one per address Alps serves), normally starting at port **18443**, and adds a launcher tile. The tile opens the app on the address you are using Alps from.
Sign in to Alps first. Opening the app while signed out takes you to the Alps login and
then back to the app. Caddy uses the same private certificate authority as Alps, so your
existing certificate trust applies. If your firewall is enabled, allow that HTTPS port on the networks Alps serves.

Alps must use HTTPS. Exposing a site does not start its server; manage it in Aeterna or
its usual service. It does not start or re-enable the Alps service either.

Choose a port or icon:

```sh
alps expose http://127.0.0.1:3000 --name Notebook --port 18444 --icon '~/Pictures/notebook.svg'
```

Repeat with the same name to change its upstream or settings. Its URL stays on the same
HTTPS port unless you pass a different `--port`. Names produce slugs such as `car-search`.
Renaming a tile in the launcher persists when routes are reapplied or settings change.
Its CLI slug stays the same; use the original `--name` when updating its exposure.

```sh
alps list
alps unexpose notebook
```

`unexpose` removes both the proxy route and the tile. It leaves the original app running.
Proxy addresses and removal are managed in the CLI; names, icons and app order can be changed in the launcher.
If the Alps computer's IP changes, update Alps setup and run `alps apply` to update the proxy addresses.
Also run `alps apply` after updating Alps, so the proxy routes pick up any security fixes.

The CLI asks for sudo to validate and reload Caddy. It changes only Alps' managed Caddy
snippet and restores it if validation or reload fails. No proxy can be added through the web UI.

## Access

The CLI refuses ports that also listen outside loopback, because that would let someone
bypass Alps. Keep the upstream bound to loopback after exposing it. Other processes on the
same computer can still access a loopback server directly.

Only expose apps you trust. Browsers share cookies between ports on the same hostname;
Alps removes its own session cookie before passing a request to the upstream. Sign-out
blocks new requests; an already open WebSocket can continue until it disconnects.

Port suggestions are available only after sign-in. They check existing local listeners,
not other computers, and exclude common database/admin ports and non-web responses.
Discovery does not open firewall ports or expose a localhost server.

The installer adds `alps` to `~/.local/bin`. If your shell cannot find it, use
`~/Void/Alps/alps` directly or add `~/.local/bin` to your PATH. Reinstall just the command with:

```sh
./alps install-command
```
