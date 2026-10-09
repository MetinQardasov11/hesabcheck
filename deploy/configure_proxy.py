"""Provision this domain's HTTP/HTTPS hosts, preserving all other virtual hosts."""
import datetime
import pathlib
import re
import subprocess

CONFIG = pathlib.Path('/root/hospital-appointment-demo/nginx.conf')
PROXY = 'hospital-appointment-demo-nginx-1'
DOMAIN = 'hesabcheck.testgrelo.online'
ROOT = pathlib.Path('/root/hesabcheck')


def replace_hosts(original, replacement):
    matches = []
    for start in re.finditer(r'(?m)^server\s*\{', original):
        depth = 0
        for offset in range(original.index('{', start.start()), len(original)):
            depth += (original[offset] == '{') - (original[offset] == '}')
            if depth == 0:
                block = original[start.start():offset + 1]
                if re.search(r'server_name\s+' + re.escape(DOMAIN) + r'\s*;', block):
                    matches.append((start.start(), offset + 1))
                break
    for start, end in reversed(matches):
        original = original[:start] + original[end:]
    return original.rstrip() + '\n\n' + replacement.strip() + '\n'


def apply_config(text):
    previous = CONFIG.read_text()
    if previous == text:
        return
    try:
        # Preserve inode: Docker bind-mounts this individual file.
        CONFIG.write_text(text)
        subprocess.run(['docker', 'exec', PROXY, 'nginx', '-t'], check=True)
        subprocess.run(['docker', 'exec', PROXY, 'nginx', '-s', 'reload'], check=True)
    except BaseException:
        CONFIG.write_text(previous)
        subprocess.run(['docker', 'exec', PROXY, 'nginx', '-t'], check=True)
        subprocess.run(['docker', 'exec', PROXY, 'nginx', '-s', 'reload'], check=True)
        raise


def main():
    original = CONFIG.read_text()
    timestamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
    backup = ROOT / 'backups' / f'nginx-before-{timestamp}.conf'
    backup.write_text(original)
    backup.chmod(0o600)
    http = f'''server {{
    listen 80;
    server_name {DOMAIN};
    location ^~ /.well-known/acme-challenge/ {{
        root /var/www/certbot;
        default_type "text/plain";
        try_files $uri =404;
    }}
    location / {{ return 301 https://$host$request_uri; }}
}}'''
    certificate = pathlib.Path('/etc/letsencrypt/live') / DOMAIN / 'fullchain.pem'
    if not certificate.exists():
        apply_config(replace_hosts(original, http))
        # Existing server account and renewal timer are reused. DNS must point here.
        subprocess.run(['certbot', 'certonly', '--webroot', '-w', '/root/certbot-webroot',
                        '--non-interactive', '--agree-tos', '--cert-name', DOMAIN, '-d', DOMAIN], check=True)
    https = pathlib.Path(__file__).with_name('nginx-https.conf').read_text()
    https = https[https.index('server {'):]
    apply_config(replace_hosts(CONFIG.read_text(), http + '\n\n' + https))
    print('HesabCheck domain configured with HTTPS; other virtual hosts preserved.')


if __name__ == '__main__':
    main()
