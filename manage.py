#!/usr/bin/env python3
"""Offline bootstrap and release management. Never discovers production credentials."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import plistlib
import shutil
import subprocess
import sys
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parent
LABELS = {'speech-notes':'org.personalagent.oss.speech-notes', 'worker':'org.personalagent.oss.worker', 'voice':'org.personalagent.oss.voice', 'calendar':'org.personalagent.oss.calendar', 'tools':'org.personalagent.oss.tools'}

def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2)+'\n');path.chmod(0o600)

def init(home, role, timezone):
    home=home.resolve()
    import secrets
    ZoneInfo(timezone)
    if home.exists():raise ValueError('Target already exists; refusing to replace private configuration')
    home.mkdir(parents=True,mode=0o700)
    for folder in ('config','data','logs','releases','services','codex-home'):(home/folder).mkdir(mode=0o700)
    config=home/'config';data=home/'data'
    if role=='broker':
        cfg=json.loads((REPO/'config/server.example.json').read_text())
        cfg.update(database=str(data/'state.sqlite3'),client_token=secrets.token_urlsafe(32),worker_token=secrets.token_urlsafe(32))
        cfg['profile']['timezone']=timezone
        write_json(config/'server-config.json',cfg)
        write_json(config/'client-setup.json',dict(url='https://agent.example.com/personal-agent/briefing',headers={'Authorization':'Bearer '+cfg['client_token']}))
    else:
        cfg=json.loads((REPO/'config/worker.example.json').read_text())
        cfg.update(state_dir=str(data),codex=shutil.which('codex') or '/absolute/path/to/codex',codex_home=str(home/'codex-home'),instructions=str(config/'assistant-instructions.txt'),voice_inbox_dir=str(data/'voice-inbox'),qwen_asr_config=str(config/'qwen-asr.json'))
        write_json(config/'worker-config.json',cfg)
        shutil.copyfile(REPO/'src/assistant-instructions.txt',config/'assistant-instructions.txt');(config/'assistant-instructions.txt').chmod(0o600)
        for name in ('qwen-asr','zello'):shutil.copyfile(REPO/f'config/{name}.example.json',config/f'{name}.json' if name=='qwen-asr' else config/'zello-config.json')
        for p in config.iterdir():p.chmod(0o600)
    write_json(home/'deployment.json',dict(role=role,timezone=timezone,format=1))
    print('Initialized private deployment:',home)
    print('Edit config files locally. No service started; no account copied.')

def metadata(home):return json.loads((home/'deployment.json').read_text())

def doctor(home):
    home=home.resolve()
    meta=metadata(home);role=meta['role'];cfg=json.loads((home/'config'/('server-config.json' if role=='broker' else 'worker-config.json')).read_text());problems=[]
    def require(ok,label):
        print(('OK   ' if ok else 'FAIL ')+label)
        if not ok:problems.append(label)
    require(sys.version_info>=(3,11),'Python >= 3.11')
    require(not home.stat().st_mode&0o077,'Private deployment directory mode 700')
    for p in (home/'config').iterdir():require(not p.stat().st_mode&0o077,'Private config permissions: '+p.name)
    require(Path(cfg.get('state_dir',str(home/'data'))).resolve()==home/'data','State belongs to this deployment')
    if role=='broker':
        require(len(cfg.get('client_token',''))>=32 and len(cfg.get('worker_token',''))>=32,'Broker tokens generated')
        require(cfg.get('client_token')!=cfg.get('worker_token'),'Separate client/worker roles')
        require(Path(cfg['database']).resolve().parent==home/'data','Database belongs to this deployment')
    else:
        require(sys.platform=='darwin','Mac worker platform')
        require(cfg['base_url'].startswith('https://') and 'example.com' not in cfg['base_url'],'Configured HTTPS broker')
        require(len(cfg.get('worker_token',''))>=32 and 'COPY_' not in cfg['worker_token'],'Configured worker token')
        require(Path(cfg['codex']).is_file() and os.access(cfg['codex'],os.X_OK),'Codex executable')
        require(Path(cfg['codex_home']).resolve()==home/'codex-home','Isolated Codex home')
        require(Path(cfg['instructions']).is_file(),'Instructions file')
        # No account login or network call during doctor.
        if cfg.get('zello_duplex_enabled'):
            require(cfg.get('voice_input_engine')=='qwen3_asr','Supported fixed ASR engine')
            require(cfg.get('voice_engine') in ('macos','gpt_sovits'),'Supported speech engine')
            for key in ('voice_asr_python','voice_asr_model_dir','qwen_asr_config'):require(Path(cfg[key]).exists(),key)
            for binary in ('node','opusenc','opusdec'):require(bool(shutil.which(binary)),binary)
            node=shutil.which('node')
            if node:
                r=subprocess.run([node,'-e','process.exit(typeof WebSocket === "function" ? 0 : 1)'],capture_output=True)
                require(r.returncode==0,'Node has built-in WebSocket')
            z=json.loads((home/'config/zello-config.json').read_text())
            require(z.get('receiver')!=z.get('username'),'Separate phone and agent Zello accounts')
            require(bool(z.get('receiver')) and z.get('receiver')!='PHONE_ACCOUNT','Zello receiver configured')
            require(z.get('password')!='SET_LOCALLY' and z.get('issuer')!='SET_LOCALLY','Zello credentials configured')
            require(Path(z.get('private_key','')).is_file(),'Zello private key exists')
            if cfg.get('voice_engine')=='gpt_sovits':
                for key in ('gpt_python','gpt_voice_root','tts_reference_audio','tts_reference_text'):require(bool(cfg.get(key)) and Path(cfg[key]).exists(),key)
    return not problems

def install(home):
    home=home.resolve()
    if not doctor(home):raise ValueError('Fix doctor failures before installing')
    meta=metadata(home);digest=hashlib.sha256()
    files=sorted(p for p in (REPO/'src').rglob('*') if p.is_file() and '__pycache__' not in p.parts)
    for p in files:digest.update(str(p.relative_to(REPO)).encode());digest.update(p.read_bytes())
    version=(REPO/'VERSION').read_text().strip()+'-'+digest.hexdigest()[:12]
    release=home/'releases'/version
    if not release.exists():
        shutil.copytree(REPO/'src',release,ignore=shutil.ignore_patterns('__pycache__'))
        write_json(release/'release.json',dict(version=version,python=sys.executable))
    current=home/'current'
    if current.is_symlink() and current.resolve()!=release:
        previous=home/'previous';previous.unlink(missing_ok=True);previous.symlink_to(current.resolve(),target_is_directory=True)
    elif current.exists() and not current.is_symlink():raise ValueError('current must be a managed symlink')
    temporary=home/'.current-next';temporary.unlink(missing_ok=True);temporary.symlink_to(release,target_is_directory=True);temporary.replace(current)
    env=dict(PERSONAL_AGENT_CONFIG_DIR=str(home/'config'),PERSONAL_AGENT_DATA_DIR=str(home/'data'),PERSONAL_AGENT_TIMEZONE=meta['timezone'],PATH=os.environ.get('PATH','/usr/bin:/bin'))
    if meta['role']=='broker':
        import shlex
        env['PERSONAL_AGENT_CONFIG']=str(home/'config/server-config.json')
        # systemd paths containing whitespace need explicit quoting.
        quote=lambda x:'"'+str(x).replace('\\','\\\\').replace('"','\\"').replace('%','%%')+'"'
        text='[Unit]\nDescription=Personal Agent broker\nAfter=network.target\n[Service]\nType=simple\n'
        text+='ExecStart='+quote(sys.executable)+' -u '+quote(current/'server.py')+'\n'
        text+=''.join('Environment='+quote(k+'='+v)+'\n' for k,v in env.items())
        text+='Restart=on-failure\nRestartSec=5\nUMask=0077\nNoNewPrivileges=true\nPrivateTmp=true\n[Install]\nWantedBy=default.target\n'
        (home/'services/personal-agent-oss.service').write_text(text)
    else:
        cfg=json.loads((home/'config/worker-config.json').read_text())
        mirror_policy=home/'data/speech-notes/config.json'
        mirror_enabled=mirror_policy.exists() and json.loads(mirror_policy.read_text()).get('enabled',False)
        if mirror_enabled:
            write_plist(home,'speech-notes',[sys.executable,'-u',str(current/'speech_notes.py'),'--config',str(home/'config/worker-config.json'),'--root',str(home/'data/speech-notes')],env)
        names=['worker']+(['voice'] if cfg.get('zello_duplex_enabled') else [])
        scripts={'worker':'worker.py','voice':'zello_delivery.py'}
        for name in names:
            program=[sys.executable,'-u',str(current/scripts[name])]
            write_plist(home,name,program,env)
        for name,enabled,app,exe in [('calendar',True,'Personal Agent Calendar OSS.app','calendar-bridge'),('tools',cfg.get('pim_tools_enabled',False) or mirror_enabled,'Personal Agent Tools OSS.app','pim-bridge')]:
            binary=Path.home()/'Applications'/app/'Contents/MacOS'/exe
            if enabled and binary.exists():write_plist(home,name,[str(binary)],env)
    print('Installed release:',version)
    print('Service definitions:',home/'services')
    print('No service started. See docs/DEPLOYMENT.md for explicit activation and rollback.')

def write_plist(home,name,program,env):
    value=dict(Label=LABELS[name],ProgramArguments=program,EnvironmentVariables=env,RunAtLoad=True,KeepAlive=True,StandardOutPath=str(home/'logs'/f'{name}.log'),StandardErrorPath=str(home/'logs'/f'{name}.error.log'),Umask=0o077)
    (home/'services'/f'{LABELS[name]}.plist').write_bytes(plistlib.dumps(value))

def rollback(home):
    home=home.resolve()
    previous=home/'previous'
    if not previous.is_symlink() or not previous.resolve().is_dir():raise ValueError('No previous release')
    target=previous.resolve();current=home/'current';old=current.resolve()
    temporary=home/'.rollback';temporary.unlink(missing_ok=True);temporary.symlink_to(target,target_is_directory=True);temporary.replace(current)
    previous.unlink();previous.symlink_to(old,target_is_directory=True)
    print('Code rolled back; configuration/database unchanged. Restart installed services explicitly.')

def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--home',required=True,type=Path)
    sub=parser.add_subparsers(dest='command',required=True)
    p=sub.add_parser('init');p.add_argument('--role',choices=['broker','mac'],required=True);p.add_argument('--timezone',default='UTC')
    for name in ('doctor','install','rollback'):sub.add_parser(name)
    p=sub.add_parser('backup');p.add_argument('--output',required=True,type=Path);p.add_argument('--include-health',action='store_true')
    p=sub.add_parser('restore');p.add_argument('--input',required=True,type=Path)
    args=parser.parse_args();home=args.home.expanduser().resolve()
    if home==REPO or REPO in home.parents:parser.error('Private deployment must be outside the public repository')
    os.umask(0o077)
    try:
        if args.command=='init':init(home,args.role,args.timezone)
        elif args.command=='doctor':sys.exit(0 if doctor(home) else 1)
        elif args.command=='install':install(home)
        elif args.command=='rollback':rollback(home)
        else:
            from tools.migration import backup,restore
            if args.command=='backup':backup(home,args.output.expanduser().resolve(),args.include_health)
            else:restore(home,args.input.expanduser().resolve())
    except (ValueError,OSError,KeyError) as exc:parser.exit(1,str(exc)+'\n')

if __name__=='__main__':main()
