"""Update only this application's HTTPS virtual host in the shared proxy."""
import datetime
import pathlib
import re
import subprocess

CONFIG = pathlib.Path('/root/hospital-appointment-demo/nginx.conf')
PROXY = 'hospital-appointment-demo-nginx-1'
DOMAIN = 'history.testgrelo.online'
ROOT = pathlib.Path('/root/hesabcheck')


def main():
    original = CONFIG.read_text()
    replacement = pathlib.Path(__file__).with_name('nginx-https.conf').read_text().strip()
    replacement = replacement[replacement.index('server {'):]
    matches = []
    for start in re.finditer(r'(?m)^server\s*\{', original):
        depth = 0
        for offset in range(original.index('{', start.start()), len(original)):
            depth += (original[offset] == '{') - (original[offset] == '}')
            if depth == 0:
                block = original[start.start():offset + 1]
                if re.search(r'server_name\s+' + re.escape(DOMAIN) + r'\s*;', block) and re.search(r'listen\s+443\s+ssl', block):
                    matches.append((start.start(), offset + 1))
                break
    if len(matches) != 1:
        raise RuntimeError('Expected exactly one existing HTTPS virtual host; no changes made.')
    start, end = matches[0]
    updated = original[:start] + replacement + original[end:]
    if updated == original:
        return
    timestamp = datetime.datetime.now(datetime.timezone.utc).strftime('%Y%m%dT%H%M%SZ')
    backup = ROOT / 'backups' / f'nginx-before-{timestamp}.conf'
    backup.write_text(original)
    backup.chmod(0o600)
    try:
        # Preserve the inode because Docker bind-mounts this individual file.
        CONFIG.write_text(updated)
        subprocess.run(['docker', 'exec', PROXY, 'nginx', '-t'], check=True)
        subprocess.run(['docker', 'exec', PROXY, 'nginx', '-s', 'reload'], check=True)
    except BaseException:
        CONFIG.write_text(original)
        subprocess.run(['docker', 'exec', PROXY, 'nginx', '-t'], check=True)
        subprocess.run(['docker', 'exec', PROXY, 'nginx', '-s', 'reload'], check=True)
        raise
    print('HesabCheck HTTPS virtual host configured; other hosts preserved.')


if __name__ == '__main__':
    main()
