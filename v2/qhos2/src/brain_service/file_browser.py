"""On-demand read-only browsing, separate from bounded automatic source indexing."""
from __future__ import annotations
import hashlib
import heapq
import os
from pathlib import Path
import re
from .log_feed import safe_message

PRIVATE_PARTS={'.ssh','.gnupg','.aws','.azure','credentials','secrets','.codex','.config','.docker','.kube'}
# Credential material named outside a private folder (QHOS-AUD-301). Matched on the
# resolved path *and* on the lexical path, before any byte is read; backup copies
# (id_rsa.bak, service.env.old) count as the original.
CREDENTIAL_SUFFIXES={'.pem','.key','.p12','.pfx','.token','.env'}
CREDENTIAL_NAMES={'.git-credentials','.netrc','.pgpass','.npmrc','.pypirc','credentials.json',
                  'id_rsa','id_dsa','id_ecdsa','id_ed25519',
                  'consolehost_history.txt','.bash_history','.zsh_history','.python_history'}
BACKUP_SUFFIXES=('.bak','.old','.orig','.backup','~')
# Key material recognised by content, whatever the file name (first chunk read).
PRIVATE_KEY_MARK=b'PRIVATE KEY-----'
_QUOTED=re.compile(r'["`]([^"`\n]+)["`]')
# Unquoted absolute path. The lookbehind keeps "s:/" inside https:// and the like out (QHOS-AUD-302).
_ABSOLUTE=re.compile(r'(?<![\w:/])(?:[A-Za-z]:[\\/]|/(?:opt|mnt|home|var|tmp)/)[^\n"`]+')
_DRIVE=re.compile(r'^[A-Za-z]:[\\/]')
_LIST_TRADER=re.compile(r'(?i)(listar|liste|pastas|diretorios|diretórios|arquivos|percorr).*(trader)|(trader).*(pastas|arquivos)')


def _sensitive_name(name):
    low=name.lower()
    stripped=True
    while stripped:
        stripped=False
        for suffix in BACKUP_SUFFIXES:
            if low.endswith(suffix) and len(low)>len(suffix):
                low=low[:-len(suffix)];stripped=True
    return low in CREDENTIAL_NAMES or Path(low).suffix in CREDENTIAL_SUFFIXES


def _refuse_credential_parts(parts):
    for part in parts:
        low=part.lower()
        if low in PRIVATE_PARTS or low.startswith('.env'):raise PermissionError('credential_location')
    if parts and _sensitive_name(parts[-1]):raise PermissionError('credential_file')


def _looks_like_path(text):
    # A quoted phrase is a path only when it has path shape (QHOS-AUD-302): a separator or a
    # drive with separator. "bom dia", "C:" or a bare word is conversation, not an authorization.
    return bool(re.search(r'[\\/]',text) or _DRIVE.match(text))


def _root_problem(p):
    """Why a configured root is unusable (QHOS-AUD-296): missing, unreadable or not a directory."""
    try:
        info=os.stat(p)
    except FileNotFoundError:return 'root_missing'
    except PermissionError:return 'root_unreadable'
    except OSError:return 'root_unavailable'
    import stat
    return None if stat.S_ISDIR(info.st_mode) else 'root_not_directory'


class FileBrowser:
    def __init__(self, target_root, roots=None):
        self.roots={'trader':Path(target_root).resolve()}
        self.unavailable_roots={}
        for name,path in (roots or {}).items():
            p=Path(path)
            problem=_root_problem(p)
            if problem is None:self.roots[name]=p.resolve()
            else:self.unavailable_roots[name]=problem

    def resolve(self, root, relative=''):
        if root not in self.roots:raise ValueError('unknown_root')
        base=self.roots[root]
        _refuse_credential_parts(Path(relative).parts)
        candidate=(base/relative).resolve(strict=True)
        if not candidate.is_relative_to(base):raise PermissionError('outside_read_root')
        _refuse_credential_parts(candidate.relative_to(base).parts)
        return candidate

    def list(self, root='trader', path='', after='', limit=100):
        directory=self.resolve(root,path)
        limit=min(max(int(limit),1),200)
        with os.scandir(directory) as entries:
            chosen=heapq.nsmallest(limit+1,(entry.name for entry in entries if entry.name>after))
        rows=[]
        for name in chosen[:limit]:
            p=directory/name
            try:
                resolved=self.resolve(root,str(p.relative_to(self.roots[root])))
                rows.append({'name':name,'path':str(p.relative_to(self.roots[root])),
                             'type':'directory' if resolved.is_dir() else 'file','size':resolved.stat().st_size})
            except (OSError,PermissionError,ValueError):rows.append({'name':name,'readable':False})
        return {'root':root,'path':path,'entries':rows,'next_after':chosen[limit-1] if len(chosen)>limit else None,'read_only':True}

    def read(self, root, path, offset=0, limit=65536):
        p=self.resolve(root,path)
        if not p.is_file():raise ValueError('not_regular_file')
        offset=max(0,int(offset));limit=min(max(int(limit),1),65536)
        with p.open('rb') as f:
            f.seek(offset);raw=f.read(limit)
        if PRIVATE_KEY_MARK in raw:raise PermissionError('credential_content')
        if b'\0' in raw:raise ValueError('binary_file_metadata_only')
        return {'root':root,'path':path,'offset':offset,'next_offset':offset+len(raw) if offset+len(raw)<p.stat().st_size else None,
                'text':safe_message(raw.decode('utf-8',errors='replace')),'chunk_sha256':hashlib.sha256(raw).hexdigest(),
                'read_only':True,'content_is_instruction':False}

    def interpret(self, text):
        """Interpretation only: which root/relative the text names, or None. Nothing is read here.

        ``explicit`` is True for an absolute path (drive or rooted) the user spelled out; a relative
        phrase that merely looks like a path is a heuristic guess and must not force anything.
        """
        quoted=[q.strip() for q in _QUOTED.findall(text) if _looks_like_path(q.strip())]
        candidates=quoted or _ABSOLUTE.findall(text)
        if not candidates:
            if _LIST_TRADER.search(text):return {'root':'trader','relative':'','explicit':True}
            return None
        path=candidates[0].strip().rstrip('?.')
        explicit=bool(_DRIVE.match(path) or path.startswith(('/','\\')))
        if re.match(r'^[Cc]:[\\/]',path) and 'computer_c' in self.roots:
            return {'root':'computer_c','relative':path[3:].replace('\\','/'),'explicit':True}
        raw=Path(path)
        matched=[(key,base) for key,base in self.roots.items() if raw.is_absolute() and raw.is_relative_to(base)]
        if not matched and not raw.is_absolute():
            matched=[('trader',self.roots['trader'])];raw=self.roots['trader']/raw
        if not matched:return {'available':False,'reason':'path_outside_configured_read_roots'}
        root,base=max(matched,key=lambda item:len(str(item[1])))
        return {'root':root,'relative':str(raw.relative_to(base)),'explicit':explicit}

    def question(self, text):
        # Files are read only when the user names a path; no background PC scan.
        target=self.interpret(text)
        if target is None or 'root' not in target:return target
        try:
            resolved=self.resolve(target['root'],target['relative'])
            return self.list(target['root'],target['relative']) if resolved.is_dir() else self.read(target['root'],target['relative'])
        except (PermissionError,ValueError) as exc:return {'available':False,'reason':str(exc)}
        except FileNotFoundError:
            # A guessed relative phrase that names nothing is conversation, not an operational request.
            return {'available':False,'reason':'path_not_found'} if target.get('explicit') else None
        except OSError as exc:return {'available':False,'reason':type(exc).__name__}

    def snapshot(self):
        return {'roots':{k:str(v) for k,v in self.roots.items()},'unavailable_roots':dict(self.unavailable_roots),
                'read_only':True,'automatic_computer_scan':False,'pagination':True,'credential_locations_excluded':True}
