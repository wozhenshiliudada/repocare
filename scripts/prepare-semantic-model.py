"""Explicit download or offline verification of the project's already pinned public model."""
import argparse
import hashlib
import json
from pathlib import Path
import sys
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from repo_maintainer.recovery import unlinked
from repo_maintainer.semantic_retrieval import FILES, MODEL, REVISION, ModelPackage

BASE = f'https://huggingface.co/{MODEL}/resolve/{REVISION}/'
DOWNLOADS = {name: (BASE + name, size, sha) for name, (size, sha) in FILES.items()}
DOWNLOADS.update({
    'README.md': (BASE + 'README.md', 3888, '1e98ea05b0de579fcaad3d625b62ea55647142ed674d5f5ebf1440e4bbbb6f23'),
    'LICENSE.txt': ('https://www.apache.org/licenses/LICENSE-2.0.txt', 11358,
                    'cfc7749b96f63bd31c3c42b5c471bf756814053e847c10f3eb003417bc523d30'),
})


def public_url(url):
    value = urlsplit(url)
    host = value.hostname or ''
    if (value.scheme != 'https' or value.username or value.password or value.port not in (None, 443)
            or not (host in {'huggingface.co', 'www.apache.org'} or host.endswith('.hf.co'))):
        raise ValueError('Unexpected model download origin')


class PublicRedirect(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, url):
        public_url(url)
        return super().redirect_request(request, response, code, message, headers, url)


def bounded_copy(response, destination, size, sha):
    digest = hashlib.sha256()
    written = 0
    with destination.open('xb') as output:
        while True:
            chunk = response.read(min(1024 * 1024, size + 1 - written))
            if not chunk:
                break
            written += len(chunk)
            if written > size:
                raise ValueError('Downloaded file exceeds pinned size; partial output retained')
            digest.update(chunk)
            output.write(chunk)
    if written != size or digest.hexdigest() != sha:
        raise ValueError('Downloaded file differs from pinned bytes; partial output retained')


def verify(directory):
    directory = unlinked(directory)
    for name, (_, size, sha) in DOWNLOADS.items():
        target = unlinked(directory / name)
        if target.stat().st_size != size:
            raise ValueError('Pinned model data size changed')
        if hashlib.sha256(target.read_bytes()).hexdigest() != sha:
            raise ValueError('Pinned model data digest changed')
    model = ModelPackage(directory / 'manifest.json')
    return {'manifest': str(model.path), 'sha256': model.sha, 'repository': MODEL,
            'revision': REVISION, 'verified': True, 'model_requests': 0}


def prepare(directory, download=False):
    directory = unlinked(directory)
    if not download:
        return verify(directory)
    # Never overwrite a previous complete or partial package, and never register it here.
    directory.mkdir(parents=True, exist_ok=False)
    opener = build_opener(PublicRedirect())
    for name, (url, size, sha) in DOWNLOADS.items():
        public_url(url)
        target = unlinked(directory / name)
        target.parent.mkdir(parents=True, exist_ok=True)
        print('Downloading pinned data: ' + name, file=sys.stderr, flush=True)
        with opener.open(Request(url, headers={'User-Agent': 'RepoCare-pinned-model/1'}), timeout=120) as response:
            public_url(response.url)
            bounded_copy(response, target, size, sha)
    manifest = {'schema_version': 1, 'repository': MODEL, 'revision': REVISION,
                'license': 'Apache-2.0', 'files': [{'path': name, 'bytes': size, 'sha256': sha}
                for name, (_, size, sha) in DOWNLOADS.items()]}
    with (directory / 'manifest.json').open('x', encoding='utf-8') as output:
        json.dump(manifest, output, ensure_ascii=False, indent=2)
        output.write('\n')
    return verify(directory)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, required=True, help='New package directory, or existing directory to verify')
    parser.add_argument('--download', action='store_true', help='Explicitly permit fixed HTTPS model downloads; existing directory is refused')
    args = parser.parse_args()
    try:
        result = prepare(args.directory, args.download)
    except Exception as error:
        # Redirect/error URLs can contain signed query strings; do not log them.
        print(json.dumps({'verified': False, 'error_type': type(error).__name__,
                          'reason': 'Preparation failed; keep the output and inspect pinned inputs. No registration performed.'}))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
