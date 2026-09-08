"""32-second edit of licensed recorded music and sound effects."""
import subprocess,wave
import numpy as np
from pathlib import Path
P=Path(__file__).resolve().parent;D=P/'full';A=P/'assets' if (P/'assets').is_dir() else P.parent/'assets';S=48000
music=np.zeros((32*S,2),np.float32);fx=music.copy()
def read(p):
    return np.frombuffer(subprocess.check_output(['ffmpeg','-v','error','-i',str(p),'-ar',str(S),'-ac','2','-f','f32le','-']),np.float32).reshape(-1,2).copy()
def put(dst,a,t,gain):
    i=round(t*S);n=min(len(a),len(dst)-i);dst[i:i+n]+=a[:n]*gain
track=read(P/'Epical-Drums-04.mp3')
opening=track[30*S:36*S].copy();opening*=np.linspace(.32,.85,len(opening))[:,None]
put(music,opening,0,1)
body=track[36*S:60*S].copy();body[-S:]*=np.linspace(1,0,S)[:,None]
put(music,body,8,.8)
whoosh=read(A/'sfx-1492.wav');hit=read(A/'sfx-1143.mp3');big=read(A/'sfx-788.mp3');rise=read(A/'sfx-790.wav')
for at in [2,3.25,4.25,5,5.5,8,13,18,23,28]:put(fx,whoosh,max(0,at-.36),.17)
for at in [0,8,13,18,23,28]:put(fx,hit,at,.25)
put(fx,big,8,.24);put(fx,rise,3.4,.27);put(fx,rise,26,.16)
for a in [music,fx]:
    a[6*S:8*S]=0;a[-S:]*=np.linspace(1,0,S)[:,None]
def save(name,a):
    with wave.open(str(D/name),'wb') as w:
        w.setnchannels(2);w.setsampwidth(2);w.setframerate(S);w.writeframes((np.clip(a,-.99,.99)*32767).astype('<i2').tobytes())
save('music.wav',music*.7);save('effects.wav',fx*.7)
save('premaster.wav',(music+fx)*.7)
subprocess.run(['ffmpeg','-y','-v','error','-i',str(D/'premaster.wav'),'-af','loudnorm=I=-14:TP=-1:LRA=9','-ar',str(S),str(D/'soundtrack.wav')],check=True)
