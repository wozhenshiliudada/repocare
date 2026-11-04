"""Verify the built wheel contains only current UI assets and original runtime licenses."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import zipfile


def verify(wheel, build):
    with zipfile.ZipFile(wheel) as archive:
        prefix='repo_maintainer/workbench_ui/'
        html=archive.read(prefix+'index.html').decode('utf-8')
        refs=set(re.findall(r'(?:src|href)="(/assets/[^"<>]+)"',html))
        if not refs or any(not re.fullmatch(r'/assets/[A-Za-z0-9_-]+\.(js|css)',r) for r in refs):
            raise ValueError('Missing or unsafe generated UI references')
        expected={prefix+'index.html',*(prefix+r.lstrip('/') for r in refs)}
        guide=build/'tutorial'
        manifest=json.loads((guide/'MANIFEST.json').read_bytes())
        guide_files={row['path']:row['sha256'] for row in manifest['files']}
        expected.update(prefix+'tutorial/'+name for name in {*guide_files,'MANIFEST.json'})
        actual={name for name in archive.namelist() if name.startswith(prefix)}
        if actual!=expected:
            raise ValueError('Wheel includes stale or missing UI assets: '+str(actual^expected))
        for name in expected:
            if archive.read(name)!=(build/name.removeprefix(prefix)).read_bytes():
                raise ValueError('Wheel UI does not match current build: '+name)
        for name, sha256 in guide_files.items():
            if hashlib.sha256(archive.read(prefix+'tutorial/'+name)).hexdigest()!=sha256:
                raise ValueError('Wheel guide does not match its manifest: '+name)
        licenses=[name for name in archive.namelist() if '/licenses/third_party/frontend/' in name]
        names={Path(name).name for name in licenses}
        if names!={'React-LICENSE','ReactDOM-LICENSE','Scheduler-LICENSE','Vite-LICENSE','Rolldown-LICENSE','Rolldown-THIRD-PARTY-LICENSE'}:
            raise ValueError('Runtime MIT licenses missing from distribution')
        for name in licenses:
            if archive.read(name)!=(Path(__file__).resolve().parents[1]/'third_party/frontend'/Path(name).name).read_bytes():
                raise ValueError('Runtime license differs from preserved upstream text')
        metadata = [name for name in archive.namelist() if name.endswith('.dist-info/METADATA')]
        if len(metadata) != 1 or b'License-Expression: AGPL-3.0-only' not in archive.read(metadata[0]).splitlines():
            raise ValueError('Missing project license expression in wheel metadata')
        project_license = metadata[0].removesuffix('METADATA') + 'licenses/LICENSE'
        if archive.read(project_license) != (Path(__file__).resolve().parents[1] / 'LICENSE').read_bytes():
            raise ValueError('Project license differs from the repository LICENSE')
        licenses.append(project_license)
    return {'wheel':wheel.name,'sha256':hashlib.sha256(wheel.read_bytes()).hexdigest(),
            'assets':sorted(actual),'licenses':licenses,'standalone_install_tested':False}


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('directory',type=Path)
    args=parser.parse_args()
    wheels=list(args.directory.glob('repo_maintainer-*.whl'))
    if len(wheels)!=1:parser.error('Expected exactly one built repo-maintainer wheel')
    print(json.dumps(verify(wheels[0],Path(__file__).resolve().parents[1]/'src/repo_maintainer/workbench_ui'),indent=2))
