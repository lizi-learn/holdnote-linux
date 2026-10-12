import argparse
import getpass
import json
import os
import subprocess
import sys
from .config import CONFIG, DATA_DIR, configure, load, secret
from .client import Memos
from .outbox import Outbox, Worker


def queue(config):return Outbox(DATA_DIR/'outbox.db',secret(config,'outbox-key'))


def main():
    os.umask(0o077)
    p=argparse.ArgumentParser(description='Hold Super, select text, release to save privately to Memos (X11).')
    s=p.add_subparsers(dest='action',required=True)
    c=s.add_parser('configure');c.add_argument('--url',required=True);c.add_argument('--token-stdin',action='store_true')
    s.add_parser('daemon');s.add_parser('restore-settings');s.add_parser('doctor');s.add_parser('status');s.add_parser('retry');s.add_parser('session-start')
    c=s.add_parser('send');c.add_argument('--stdin',action='store_true');c.add_argument('--selection',action='store_true');c.add_argument('--clipboard',action='store_true')
    args=p.parse_args()
    try:
        if args.action=='configure':
            token=sys.stdin.readline().strip() if args.token_stdin else getpass.getpass('Memos PAT (hidden): ')
            Memos(args.url,token).check();configure(args.url,token);print('Configured. Token/key stored in GNOME Secret Service, not config.json.');return
        if args.action=='restore-settings':
            from .desktop import restore_settings
            restore_settings();return
        if args.action=='session-start':
            if not CONFIG.exists():return
            names=[k for k in ['DISPLAY','XAUTHORITY','XDG_SESSION_TYPE','XDG_CURRENT_DESKTOP','DBUS_SESSION_BUS_ADDRESS'] if k in os.environ]
            subprocess.run(['systemctl','--user','import-environment',*names],check=True)
            subprocess.run(['systemctl','--user','start','holdnote.service'],check=True);return
        c=load()
        if args.action=='daemon':
            from .desktop import App
            App().run();return
        outbox=queue(c)
        if args.action in ['doctor','status']:
            result={'version':'0.1.0','desktop_session':os.environ.get('XDG_SESSION_TYPE'),'memos_url':c['memos_url'],'outbox':outbox.stats(),'config_contains_token':False}
            if args.action=='doctor':result['authentication']=Memos(c['memos_url'],secret(c,'memos-token')).check()
            print(json.dumps(result,ensure_ascii=False,indent=2));return
        if args.action=='retry':
            outbox.retry();print('Queued retries enabled. The daemon will deliver them.');return
        if args.action=='send':
            if sum([args.stdin,args.selection,args.clipboard])!=1:raise ValueError('Choose exactly one of --stdin, --selection, --clipboard')
            if args.stdin:text=sys.stdin.read()
            else:
                from .desktop import capture_selection
                text=capture_selection('clipboard' if args.clipboard else 'primary')
            if not text.strip():raise ValueError('No selected/copied text')
            if len(text)>c['max_chars']:raise ValueError('Text exceeds configured max_chars')
            tags=' '.join('#'+t for t in c.get('tags',[]));capture_id=outbox.add(text+('\n\n'+tags if tags else ''))
            def callback(kind,value):print('Delivery: '+kind)
            Worker(outbox,Memos(c['memos_url'],secret(c,'memos-token')),callback).once()
            print('Capture ID: '+capture_id);return
    except Exception as e:
        # Errors deliberately omit server bodies, selected text and bearer tokens.
        print('HoldNote: '+str(e),file=sys.stderr);raise SystemExit(1)


if __name__=='__main__':main()
