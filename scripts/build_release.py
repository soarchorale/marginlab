"""Build a source-only release; runtime/private files are never eligible."""
import hashlib
from pathlib import Path
import tarfile

FILES=('app.py','requirements.txt','README.md','AGENT.md','pixi.toml','pixi.lock','.gitignore','.gitattributes')
DIRECTORIES=('marginlab','scripts','static','templates','docs','deploy','tests')


def build(root,output):
    root,output=Path(root),Path(output)
    selected=[root/name for name in FILES if (root/name).is_file()]
    for name in DIRECTORIES:
        selected.extend(p for p in (root/name).rglob('*') if p.is_file()
                        and '__pycache__' not in p.parts and p.suffix not in ('.pyc','.pyo')
                        and not any(part.startswith('.') for part in p.relative_to(root/name).parts))
    output.parent.mkdir(parents=True,exist_ok=True)
    with tarfile.open(output,'w:gz') as archive:
        for path in sorted(selected):
            if path.is_symlink():raise ValueError('Symbolic links are not allowed in release archives')
            archive.add(path,arcname=str(Path('marginlab')/path.relative_to(root)),recursive=False)
    digest=hashlib.sha256(output.read_bytes()).hexdigest()
    output.with_suffix(output.suffix+'.sha256').write_text(digest+'  '+output.name+'\n')
    return len(selected)

if __name__=='__main__':
    root=Path(__file__).resolve().parents[1]
    target=root/'dist/marginlab-0.1.0.tar.gz'
    print('Source release:',target,'files:',build(root,target))
