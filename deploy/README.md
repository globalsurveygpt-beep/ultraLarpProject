# Deployment (Ubuntu 24.04 VPS, Caddy, systemd)

The demo runs on one VPS. Caddy terminates HTTPS and forwards to uvicorn on localhost; uvicorn
serves the API and the interface (`web/`). The calculation reads only files on the server disk.

## Files here

- `lucy.service`: systemd unit for the application (user `lucy`, one uvicorn process).
- `Caddyfile`: HTTPS, compression, cache headers, request size limit.

## Steps

As root, once:

```bash
apt update && apt upgrade -y
adduser lucy && usermod -aG sudo lucy
# copy your SSH public key to /home/lucy/.ssh/authorized_keys, then turn off password login
ufw allow OpenSSH && ufw allow 80 && ufw allow 443 && ufw enable
apt install -y python3-venv git caddy
```

As `lucy`:

```bash
git clone https://github.com/globalsurveygpt-beep/ultraLarpProject.git
cd ultraLarpProject
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python scripts/fetch_data.py                 # about 235 MB into data/
```

Copy the precomputed search horizons from the development machine (25 MB; computing them on the
server takes about half an hour on 4 cores):

```bash
# on the development machine, in the repository root
tar czf horizons.tgz -C data/cache horizons
scp horizons.tgz lucy@<server>:ultraLarpProject/
# on the server
mkdir -p data/cache && tar xzf horizons.tgz -C data/cache && rm horizons.tgz
```

Check the result on the server's own platform, then start the service:

```bash
.venv/bin/pip install -r requirements-dev.txt
.venv/bin/pytest -q                                    # includes the real-data and example-scenario tests
sudo cp deploy/lucy.service /etc/systemd/system/lucy.service
sudo systemctl enable --now lucy
```

HTTPS. In Cloudflare add an `A` record for the site name pointing to the server, first as
"DNS only" (grey cloud) so that Caddy can obtain its certificate. Then:

```bash
sudo cp deploy/Caddyfile /etc/caddy/Caddyfile
sudo sed -i 's/{$LUCY_HOST}/<site name>/' /etc/caddy/Caddyfile
sudo systemctl reload caddy
```

When `https://<site name>` works, switch the record to "Proxied" (orange cloud) and set
SSL/TLS to "Full (strict)".

## Load protection

- In the application: at most two calculations at once; a request that cannot get a slot within
  25 seconds receives `503` "the server is busy". Search periods are limited to 90 days.
- Horizon cache: the search grid (`data/cache/horizons`) is permanent; sites that visitors ask for
  one by one go to `data/cache/horizons-extra`, which keeps the newest 2000 entries (a few KB each).
- Cloudflare, one rate limiting rule (free plan): if the path starts with `/api/`, more than
  20 requests in 10 seconds from one IP, block for 10 seconds. One click on the interface makes
  about five API calls.

## Updating and rolling back

```bash
cd ~/ultraLarpProject && git pull && .venv/bin/pip install -r requirements.txt && sudo systemctl restart lucy
git checkout <earlier commit> && sudo systemctl restart lucy     # rollback; data and cache are untouched
```

## Checks

- `https://<site name>` opens, the certificate is valid, the page has no requests to other hosts.
- The four example scenarios give the same results as on the development machine.
- `/docs` and `/openapi.json` answer 404.
- After `sudo reboot` the site comes back by itself.
