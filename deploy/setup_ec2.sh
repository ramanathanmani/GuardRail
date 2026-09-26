#!/usr/bin/env bash
# Run this ON a fresh Ubuntu 22.04/24.04 EC2 instance (ap-south-1), after you've SSHed in.
# It does not touch AWS itself — the EC2 instance, Elastic IP, security group, DNS record and
# IAM role (s3:PutObject on your audit bucket) are console/CLI steps you do by hand first.
#
# Usage: REPO_URL=https://github.com/you/guardrail.git ./setup_ec2.sh
set -euo pipefail

REPO_URL="${REPO_URL:?Set REPO_URL to your git remote, e.g. REPO_URL=... ./setup_ec2.sh}"
APP_DIR=/opt/guardrail

echo "== Python 3.12 =="
sudo apt-get update
sudo apt-get install -y software-properties-common curl git
sudo add-apt-repository -y ppa:deadsnakes/ppa
sudo apt-get update
sudo apt-get install -y python3.12 python3.12-venv

echo "== Node (for the frontend build) =="
if ! command -v node >/dev/null; then
	curl -fsSL https://deb.nodesource.com/setup_20.x | sudo -E bash -
	sudo apt-get install -y nodejs
fi

echo "== Caddy =="
sudo apt-get install -y debian-keyring debian-archive-keyring apt-transport-https
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
	| sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
	| sudo tee /etc/apt/sources.list.d/caddy-stable.list
sudo apt-get update
sudo apt-get install -y caddy

echo "== Clone / update repo =="
if [ -d "$APP_DIR/.git" ]; then
	git -C "$APP_DIR" pull
else
	sudo mkdir -p "$APP_DIR"
	sudo chown "$USER":"$USER" "$APP_DIR"
	git clone "$REPO_URL" "$APP_DIR"
fi

echo "== Backend venv =="
cd "$APP_DIR/backend"
python3.12 -m venv .venv
./.venv/bin/pip install -r requirements.txt

echo "== Frontend build =="
cd "$APP_DIR/frontend"
npm install
npm run build

echo "== systemd unit =="
sudo cp "$APP_DIR/deploy/guardrail.service" /etc/systemd/system/guardrail.service
sudo systemctl daemon-reload
sudo systemctl enable guardrail

echo "== Caddyfile =="
sudo cp "$APP_DIR/deploy/Caddyfile" /etc/caddy/Caddyfile

cat <<'EOF'

Now, from your laptop:
  scp backend/.env      <host>:/opt/guardrail/backend/.env
  scp deploy/caddy.env  <host>:/opt/guardrail/deploy/caddy.env

Then on the server:
  sudo systemctl restart guardrail
  sudo systemctl status guardrail

  # Point Caddy at deploy/caddy.env for SITE_ADDRESS / DASHBOARD_USER / DASHBOARD_PASSWORD_HASH:
  sudo systemctl edit caddy
  #   add under [Service]:
  #   EnvironmentFile=/opt/guardrail/deploy/caddy.env
  sudo systemctl restart caddy

Verify:
  curl https://<SITE_ADDRESS>/health
  # then check basic auth on the dashboard and that /ws connects over WSS (§5.7)
EOF
