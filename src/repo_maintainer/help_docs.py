"""Packaged Workbench guides for people and the assistant's read-only tool."""
from importlib.resources import files
import base64
import hashlib
import io
import json
import re
from zipfile import ZIP_DEFLATED, ZipFile
from .dashboard import unlinked
from .models import SafetyError


def help_document(language='zh-CN'):
    if language not in {'zh-CN','en'}:
        raise SafetyError('不支持的教程语言')
    root=files('repo_maintainer').joinpath('help_assets')
    content=root.joinpath('user-guide.'+language+'.md').read_text(encoding='utf-8')
    catalog=json.loads(root.joinpath('capabilities.json').read_text(encoding='utf-8'))
    return {'title':'RepoCare 使用教程' if language=='zh-CN' else 'RepoCare user guide',
            'content':content,'sha256':hashlib.sha256(content.encode('utf-8')).hexdigest(),
            'catalog':[{key:row.get(key,'') for key in ('id','title','page','availability','description')} for row in catalog]}


def illustrated_guide(build):
    """Return only the explicitly packaged files and a script-free page policy."""
    root = unlinked(build/'tutorial')
    manifest = unlinked(root/'MANIFEST.json')
    if not manifest.is_file():
        return {}, None
    index = json.loads(manifest.read_bytes())
    names = {row['path'] for row in index['files']}
    if (len(names) != index['unique_screenshots'] + 2 or not {'index.html','README.md'} <= names
            or any(not re.fullmatch(r'(?:index\.html|README\.md|images/[0-9]+-[a-z0-9-]+\.jpg)', name) for name in names)):
        raise SafetyError('Invalid illustrated guide manifest')
    assets = {name: unlinked(root/name) for name in sorted(names | {'MANIFEST.json'})}
    if not all(path.is_file() for path in assets.values()):
        return {}, None
    styles = re.findall(rb'<style>(.*?)</style>', assets['index.html'].read_bytes(), re.S)
    if len(styles) != 1:
        raise SafetyError('Invalid illustrated guide stylesheet')
    digest = base64.b64encode(hashlib.sha256(styles[0]).digest()).decode('ascii')
    policy = (f"default-src 'none'; style-src 'sha256-{digest}'; img-src 'self'; "
              "frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
    return assets, policy


def illustrated_zip(assets):
    archive = io.BytesIO()
    with ZipFile(archive, 'w', ZIP_DEFLATED) as package:
        for name, path in assets.items():
            package.write(unlinked(path), 'RepoCare-illustrated-guide/'+name)
    return archive.getvalue()
