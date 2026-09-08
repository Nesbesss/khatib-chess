import subprocess,wave
from pathlib import Path
import numpy as np
P=Path(__file__).resolve().parent;S=48000
def read(name,af='anull'):
    return np.frombuffer(subprocess.check_output(['ffmpeg','-v','error','-i',str(P/name),'-af',af,'-ar',str(S),'-ac','2','-f','f32le','-']),np.float32).reshape(-1,2).copy()
def fade(a,fi=.02,fo=.08):
    a=a.copy();n=min(int(fi*S),len(a));m=min(int(fo*S),len(a))
    if n:a[:n]*=np.linspace(0,1,n)[:,None]
    if m:a[-m:]*=np.linspace(1,0,m)[:,None]
    return a
music=np.zeros((32*S,2),np.float32);fx=music.copy()
def put(dst,a,t,g=1):
    i=round(t*S);n=min(len(a),len(dst)-i)
    if n>0:dst[i:i+n]+=a[:n]*g
track=read('Deep-Techno-Ambience.mp3')
# Locate a real percussive onset and align it with the almost-complete name entrance.
hop=240
energy=np.sqrt(np.mean(track[:len(track)//hop*hop].reshape(-1,hop,2)**2,axis=(1,2)))
onset=np.maximum(energy-np.roll(energy,2),0)
lo,hi=int(28*S/hop),int(40*S/hop)
start=(lo+np.argmax(onset[lo:hi]))*hop
reveal=11.35
opening=fade(track[max(0,start-6*S):start],.12,.02)
opening*=np.linspace(.25,.75,len(opening))[:,None];put(music,opening,0)
# Filtered tonal prelude follows the pullback, leaving the reveal transient exposed.
low=read('Deep-Techno-Ambience.mp3','lowpass=f=650')
build=fade(low[start-int(3.2*S):start],.15,.04)
build*=np.linspace(.12,.65,len(build))[:,None];put(music,build,8)
put(music,fade(track[start:start+int((32-reveal)*S)],.003,1.2),reveal,.8)
sweep=read('sweep.mp3','highpass=f=180,lowpass=f=6500')
click=read('click.mp3','lowpass=f=4500')
power=read('power.mp3','lowpass=f=5000')
def norm(a):return a/max(.01,float(np.max(np.abs(a))))
sweep=norm(sweep);click=norm(click);power=norm(power)
# Motion cues anticipate edits; quiet contact ticks follow the tile assembly.
for t,g in [(1.8,.11),(3.1,.10),(4.1,.12),(5.35,.14),(12.8,.10),(17.8,.10),(22.8,.12),(27.8,.10)]:
    put(fx,fade(sweep[:int(.5*S)],.02,.12),t,g)
for t in [2,3.25,4.25,5,5.5,9.9,10.18,10.45,15.5,19.8,20.4]:
    put(fx,fade(click[:int(.18*S)],.002,.07),t,.10)
# Recorded power-up stretched to match the 3D reveal; no old trailer impacts.
duration=3.1;idx=np.linspace(0,len(power)-1,int(duration*S))
ramp=np.column_stack([np.interp(idx,np.arange(len(power)),power[:,c]) for c in range(2)])
ramp*=np.linspace(.03,.3,len(ramp))[:,None];put(fx,fade(ramp,.12,.06),8.15)
put(fx,fade(power[:int(.65*S)],.003,.25),reveal,.24)
for a in [music,fx]:a[6*S:8*S]=0;a[-int(1.1*S):]*=np.linspace(1,0,int(1.1*S))[:,None]
def save(name,a):
    with wave.open(str(P/name),'wb') as w:
        w.setnchannels(2);w.setsampwidth(2);w.setframerate(S);w.writeframes((np.clip(a,-.99,.99)*32767).astype('<i2').tobytes())
save('music.wav',music*.75);save('effects.wav',fx*.75);save('premaster.wav',(music+fx)*.75)
subprocess.run(['ffmpeg','-y','-v','error','-i',str(P/'premaster.wav'),'-af','loudnorm=I=-15:TP=-1:LRA=10','-ar',str(S),str(P/'soundtrack.wav')],check=True)
print('Music onset aligned at',reveal,'seconds; source onset',start/S)
