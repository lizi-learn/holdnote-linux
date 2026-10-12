"""Non-secret configuration; secrets only in the GNOME Secret Service."""
import json
import os
from pathlib import Path
import uuid
from urllib.parse import urlsplit
from cryptography.fernet import Fernet

CONFIG_DIR=Path(os.environ.get('XDG_CONFIG_HOME',Path.home()/'.config'))/'holdnote'
DATA_DIR=Path(os.environ.get('XDG_DATA_HOME',Path.home()/'.local/share'))/'holdnote'
CONFIG=CONFIG_DIR/'config.json'


def private_dir(path):
    path.mkdir(mode=0o700,parents=True,exist_ok=True)
    if path.is_symlink():raise RuntimeError('HoldNote state directory must not be a symlink')
    path.chmod(0o700)


def private_write(path,data):
    private_dir(path.parent)
    tmp=path.with_name(path.name+'.tmp-'+uuid.uuid4().hex)
    fd=os.open(tmp,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'w') as f:f.write(data);f.flush();os.fsync(f.fileno())
    os.replace(tmp,path)


def normalize_url(url):
    u=urlsplit(url.strip())
    if u.username or u.password or u.query or u.fragment:raise ValueError('URL cannot contain credentials, query or fragment')
    if u.scheme!='https' and not(u.scheme=='http' and u.hostname in ['localhost','127.0.0.1','::1']):raise ValueError('HTTPS required except localhost development')
    if not u.hostname:raise ValueError('Memos URL is required')
    return url.strip().rstrip('/')


def vault():
    # Reject opportunistic plaintext keyring fallbacks.
    from keyring.backends.SecretService import Keyring
    return Keyring()


def load():
    value=json.loads(CONFIG.read_text())
    value['memos_url']=normalize_url(value['memos_url'])
    return value


def secret(config,name):
    value=vault().get_password('holdnote:'+config['profile_id'],name)
    if not value:raise RuntimeError('Secret Service has no '+name+'; run holdnote configure')
    return value


def configure(url,token):
    value=load() if CONFIG.exists() else {'profile_id':uuid.uuid4().hex,'hold_ms':450,'max_chars':20000,'tags':['inbox','holdnote']}
    value['memos_url']=normalize_url(url)
    if not token.startswith('memos_pat_'):raise ValueError('Use a Memos personal access token, not a login cookie')
    v=vault();svc='holdnote:'+value['profile_id'];v.set_password(svc,'memos-token',token)
    if not v.get_password(svc,'outbox-key'):v.set_password(svc,'outbox-key',Fernet.generate_key().decode())
    private_write(CONFIG,json.dumps(value,ensure_ascii=False,indent=2)+'\n')
    private_dir(DATA_DIR)
    return value
