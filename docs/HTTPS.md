# Trust Alps in your browser

System certificate trust and browser certificate trust can be separate on Linux.
If you still see a certificate warning after running the installer's trust commands,
import Caddy's root certificate in the browser too. Do this once per browser/device.

From the Alps folder, export the public root certificate:

```sh
sudo install -m 644 /var/lib/caddy/.local/share/caddy/pki/authorities/local/root.crt ./alps-root.crt
```

Copy only this public certificate, never `root.key`.

## Chrome on Linux

1. Open `chrome://certificate-manager/localcerts/usercerts` in Chrome's address bar.
2. Under **Trusted Certificates**, select **Import**.
3. Choose the `alps-root.crt` you exported above. Confirm any trust prompt.
4. Fully quit and reopen Chrome, then open your Alps HTTPS address again.

You can also reach this through `chrome://certificate-manager` → **Local certificates**
→ **Installed by you**. Choose **Trusted Certificates**, not **Intermediate Certificates**
or **Your certificates** (which is for client certificates).

On older Chrome versions, open `chrome://settings/certificates`, choose **Authorities → Import**,
and enable trust for identifying websites if prompted.

See the [Chromium certificate manager documentation](https://chromium.googlesource.com/chromium/src/+/main/net/data/ssl/chrome_root_store/faq.md#what_s-the-chrome-certificate-manager).

## Firefox on Linux

1. Open **Settings → Privacy & Security → Certificates → View Certificates**.
2. Select **Authorities → Import**.
3. Choose the same `alps-root.crt`.
4. Enable **Trust this CA to identify websites**, then confirm.
5. Reload Alps; restart Firefox if the warning persists.

See [Mozilla's certificate trust guidance](https://support.mozilla.org/en-US/kb/setting-certificate-authorities-firefox).

## Other devices and remaining warnings

Transfer `alps-root.crt` to each device using the installer's `scp` example or USB, then
import it into that device/browser's trusted CA store. The same root covers apps exposed
through the Alps CLI.

Use the exact HTTPS address printed by setup. If the IP changes, rerun setup and use the
new address. A certificate name mismatch needs the correct address, not another trust import.

`ERR_SSL_PROTOCOL_ERROR` can mean the linked app serves HTTP on that port. Trusting a
certificate does not turn an HTTP server into HTTPS; use its HTTP address or `alps expose`.

## Tailscale name with a trusted certificate

When setup serves a Tailscale address, it can also serve the computer's Tailscale name, for
example `https://mypc.tail1234.ts.net`. That name gets a Let's Encrypt certificate through
Tailscale. Every browser already trusts it, so no warning appears and you don't need to import
`alps-root.crt` on phones or tablets that only use this name.

Only devices on your tailnet can open the name. Every public certificate is listed in public
Certificate Transparency logs, though. Anyone can learn that the name exists, but not reach it.

Enable it once in the Tailscale admin console, from any computer:

1. Optional: if the machine name is personal, open <https://login.tailscale.com/admin/machines>,
   open this computer's **⋯** menu, and choose **Edit machine name…**. Do this first, because
   names already issued stay in the public logs.
2. Open <https://login.tailscale.com/admin/dns>.
3. Under **MagicDNS**, select **Enable MagicDNS** if it is not already on.
4. Under **HTTPS Certificates**, select **Enable HTTPS…** and confirm.
5. Run `./install.sh`, choose `lan+ips` or `ips`, pick the Tailscale address, and answer `y`
   to the Tailscale name question.

Setup adds `TS_PERMIT_CERT_UID=caddy` to `/etc/default/tailscaled`, so Caddy may ask Tailscale
for the certificate. Setup restarts Tailscale for this, which drops Tailscale connections for a
few seconds. The first visit can take a few seconds while Caddy fetches the certificate.
Tailscale renews it automatically.

LAN and Tailscale IP addresses keep using the Alps certificate, because public certificates
can't be issued for private IPs.
