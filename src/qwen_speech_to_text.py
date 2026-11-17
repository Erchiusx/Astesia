"""Resident local Qwen3-ASR adapter using the same JSON-lines protocol."""
import contextlib,json,os,sys,time,plistlib,subprocess,wave
from pathlib import Path


def main():
    os.umask(0o077)
    config=json.loads(Path(sys.argv[1]).read_text())
    q=json.loads(Path(config['qwen_asr_config']).read_text())
    if q.get('required_volume'):
        if not os.path.ismount(q['required_volume']):raise RuntimeError('Model volume is not mounted')
        volume=plistlib.loads(subprocess.check_output(['/usr/sbin/diskutil','info','-plist',q['required_volume']]))
        if q.get('volume_uuid') and volume.get('VolumeUUID')!=q['volume_uuid']:raise RuntimeError('Wrong model volume')
    os.environ.update(HF_HOME=q['hf_home'],HF_HUB_CACHE=q['hf_hub_cache'],HF_XET_CACHE=q['hf_xet_cache'],HF_HUB_OFFLINE='1',TRANSFORMERS_OFFLINE='1',TOKENIZERS_PARALLELISM='false')
    protocol=sys.stdout
    root=Path(config['voice_inbox_dir']).resolve()
    with open(os.devnull,'w') as quiet:
        with contextlib.redirect_stdout(quiet),contextlib.redirect_stderr(quiet):
            import mlx.core as mx
            import numpy as np
            from mlx_audio.stt.utils import load_model
            model=load_model(q['model_dir'],lazy=False,strict=True)
            mx.synchronize()
        print(json.dumps({'ready':True,'engine':'qwen3_asr'}),file=protocol,flush=True)
        for line in sys.stdin:
            try:
                request=json.loads(line);audio=Path(request['audio']).resolve()
                if root not in audio.parents or audio.suffix!='.wav':raise ValueError('Invalid audio file')
                with wave.open(str(audio),'rb') as wav:
                    rate=wav.getframerate()
                    if wav.getsampwidth()!=2:raise ValueError('Expected PCM16 audio')
                    samples=np.frombuffer(wav.readframes(wav.getnframes()),dtype='<i2').astype(np.float32)/32768
                if not .25<=len(samples)/rate<=95:raise ValueError('Invalid audio duration')
                if not np.isfinite(samples).all() or float(np.mean(samples**2))<1e-7:
                    print(json.dumps(dict(ok=True,text='',needs_review=True)),file=protocol,flush=True);continue
                with contextlib.redirect_stdout(quiet),contextlib.redirect_stderr(quiet):
                    result=model.generate(str(audio),max_tokens=q.get('max_tokens',1024),temperature=0,language=q.get('language'),verbose=False)
                    mx.synchronize()
                text=result.text.strip()
                print(json.dumps(dict(ok=True,text=text,needs_review=not text or len(text)>8000,engine='qwen3_asr'),ensure_ascii=False),file=protocol,flush=True)
            except Exception as exc:
                print(json.dumps(dict(ok=False,error=type(exc).__name__)),file=protocol,flush=True)


if __name__=='__main__':main()
