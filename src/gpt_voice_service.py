"""Private resident GPT-SoVITS child; JSON-lines IPC, no network listener."""
import contextlib
import json
import os
from pathlib import Path
import sys
import time


def main():
    os.umask(0o077)
    config=json.loads(Path(sys.argv[1]).read_text())
    lab=Path(config['gpt_voice_root']).resolve()
    repo=lab/'GPT-SoVITS'
    output_root=Path(config['voice_output_root']).resolve()
    os.chdir(repo)
    os.environ['is_g2pw']='False'
    os.environ['TOKENIZERS_PARALLELISM']='false'
    os.environ['HF_HUB_OFFLINE']='1'
    os.environ['TRANSFORMERS_OFFLINE']='1'
    if (lab/'nltk_data').exists():os.environ['NLTK_DATA']=str(lab/'nltk_data')
    sys.path[:0]=[str(repo),str(repo/'GPT_SoVITS')]
    protocol=sys.stdout
    # Upstream prints input text. Keep all library output out of production logs.
    with open(os.devnull,'w') as quiet:
        with contextlib.redirect_stdout(quiet),contextlib.redirect_stderr(quiet):
            import numpy as np
            import torch
            import soundfile as sf
            from scipy.signal import resample_poly
            from TTS_infer_pack.TTS import TTS,TTS_Config
            torch.set_num_threads(4)
            base=repo/'GPT_SoVITS/pretrained_models'
            engine=TTS(TTS_Config({'custom':dict(device='cpu',is_half=False,version='v2ProPlus',
                t2s_weights_path=str(base/'s1v3.ckpt'),vits_weights_path=str(base/'v2Pro/s2Gv2ProPlus.pth'),
                bert_base_path=str(base/'chinese-roberta-wwm-ext-large'),cnhuhbert_base_path=str(base/'chinese-hubert-base'))}))
            # Cache the fixed reference before reporting readiness.
            reference=Path(config['tts_reference_audio']).expanduser().resolve()
            engine.set_ref_audio(str(reference))
        print(json.dumps({'ready':True}),file=protocol,flush=True)
        for line in sys.stdin:
            started=time.monotonic()
            try:
                request=json.loads(line)
                text=request['text']
                if not isinstance(text,str) or not 1<=len(text)<=1000:
                    raise ValueError('Invalid speech length')
                output=Path(request['output']).resolve()
                if output_root not in output.parents or output.suffix!='.wav':
                    raise ValueError('Invalid output path')
                inputs=dict(text=text,text_lang='all_zh',ref_audio_path=str(reference),
                    prompt_text=Path(config['tts_reference_text']).expanduser().read_text(),prompt_lang='all_zh',
                    top_k=15,top_p=1,temperature=1,
                    text_split_method='cut0' if len(text)<=100 else 'cut4',batch_size=1,
                    speed_factor=config.get('tts_speed',.95),seed=1234,parallel_infer=False,return_fragment=False,
                    use_cuda_graph=False,use_flash_attention=False)
                pieces=[]
                with contextlib.redirect_stdout(quiet),contextlib.redirect_stderr(quiet):
                    for rate,audio in engine.run(inputs):
                        # Upstream returns PCM16; normalize before resampling.
                        value=np.asarray(audio)
                        if np.issubdtype(value.dtype,np.integer):
                            value=value.astype(np.float32)/32768
                        pieces.append(value)
                if not pieces:raise RuntimeError('Empty speech output')
                audio=np.concatenate(pieces)
                if not np.isfinite(audio).all() or np.max(np.abs(audio))<.001:
                    raise RuntimeError('Invalid speech output')
                if rate!=48000:
                    import math
                    divisor=math.gcd(rate,48000)
                    audio=resample_poly(audio,48000//divisor,rate//divisor)
                sf.write(output,audio,48000,subtype='PCM_16')
                print(json.dumps({'ok':True,'seconds':len(audio)/48000,
                    'elapsed':round(time.monotonic()-started,2)}),file=protocol,flush=True)
            except Exception as exc:
                # No raw exception details: they may contain private speech text.
                print(json.dumps({'ok':False,'error':type(exc).__name__}),file=protocol,flush=True)


if __name__=='__main__':main()
