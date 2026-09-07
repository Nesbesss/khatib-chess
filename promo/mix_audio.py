"""Edit licensed Mixkit recordings into the 40-second trailer; no synthesis."""
import subprocess,numpy as np,wave
from pathlib import Path
S=48000; out=np.zeros((40*S,2),np.float32)
def read(path,filters=None):
 cmd=['ffmpeg','-v','error','-i',str(path)]
 if filters:cmd+=['-af',filters]
 return np.frombuffer(subprocess.check_output(cmd+['-ar',str(S),'-ac','2','-f','f32le','-']),np.float32).reshape(-1,2).copy()
def add(a,start,gain=1):
 i=int(start*S);n=min(len(a),len(out)-i);out[i:i+n]+=a[:n]*gain
music=read('assets/music-160.mp3','atrim=start=0.03,asetpts=PTS-STARTPTS,atempo=0.9375')
add(music[:10*S],0,.66)
add(music[32*S:59*S],13,.7)
bed=out.copy()
# Effects occur on edits, with whooshes anticipating the cut.
whoosh=read('assets/sfx-1492.wav');impact=read('assets/sfx-1143.mp3');big=read('assets/sfx-788.mp3');riser=read('assets/sfx-790.wav');stutter=read('assets/sfx-787.wav')
for at in [2.5,5,7.5,17,21,25,29,34]:add(whoosh,max(0,at-.45),.27)
for at in [0,13,17,21,29,34]:add(impact,at,.5)
add(big,13,.44);add(riser,7.6,.6);add(stutter,8.1,.35);add(riser,31.6,.35)
# Hard cut: every stem is silent from 10 to 13 seconds.
out[10*S:13*S]=0
fx=out-bed
out[-2*S:]*=np.linspace(1,0,2*S)[:,None]
bed[10*S:13*S]=0
fx[10*S:13*S]=0
fx[-2*S:]*=np.linspace(1,0,2*S)[:,None]
bed[-2*S:]*=np.linspace(1,0,2*S)[:,None]
# Export real independent stems with shared headroom.
for name,stem in [('music-bed',bed),('effects-bed',fx)]:
 with wave.open('assets/'+name+'.wav','wb') as w:w.setnchannels(2);w.setsampwidth(2);w.setframerate(S);w.writeframes((np.clip(stem*.7,-.99,.99)*32767).astype('<i2').tobytes())
# Peak limiting then standard loudness mastering in ffmpeg.
out=np.tanh(out);out*=.88/np.max(np.abs(out))
with wave.open('assets/mix-premaster.wav','wb') as w:w.setnchannels(2);w.setsampwidth(2);w.setframerate(S);w.writeframes((out*32767).astype('<i2').tobytes())
subprocess.run(['ffmpeg','-y','-v','error','-i','assets/mix-premaster.wav','-af','loudnorm=I=-14:TP=-1:LRA=9','-ar',str(S),'assets/score.wav'],check=True)
print('Mixed online music + five recorded effect assets. 40 seconds, 48 kHz stereo.')
