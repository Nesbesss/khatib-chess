import numpy as np, wave
S=48000; D=40; rng=np.random.default_rng(73); mix=np.zeros((S*D,2))
def add(start,a,pan=0):
 i=int(start*S); n=min(len(a),len(mix)-i)
 if n>0: mix[i:i+n,0]+=a[:n]*np.sqrt((1-pan)/2);mix[i:i+n,1]+=a[:n]*np.sqrt((1+pan)/2)
def kick(at,amp=0.8):
 t=np.arange(int(.5*S))/S; a=np.sin(2*np.pi*(44*t+85*.025*(1-np.exp(-t/.025))))*np.exp(-t*10);add(at,a*amp)
def impact(at):
 t=np.arange(S*2)/S;add(at,.48*np.sin(2*np.pi*38*t)*np.exp(-t*3)+.15*rng.normal(size=len(t))*np.exp(-t*15))
def hat(at,amp=.09):
 t=np.arange(int(.08*S))/S; n=rng.normal(size=len(t));add(at,amp*(n-np.roll(n,1))*np.exp(-t*70),rng.uniform(-.65,.65))
for start,end,bpm in [(0,10,140),(13,34,140)]:
 beat=60/bpm
 for i,at in enumerate(np.arange(start,end,beat)):
  kick(at,.64 if start==0 else .8)
  if i%2: 
   t=np.arange(int(.22*S))/S;add(at,.19*rng.normal(size=len(t))*np.exp(-t*22))
  for j in range(4):hat(at+j*beat/4,.04 if j%2 else .075)
  f=[55,55,65.406,49][int(i/8)%4];t=np.arange(int(beat*.82*S))/S
  a=(np.sin(2*np.pi*f*t)+.23*np.sin(2*np.pi*f*2*t)+.12*np.sin(2*np.pi*f*3*t))*np.minimum(t*90,1)*np.exp(-t*5)
  add(at,.25*a)
  for j in range(2):
   f2=f*2**([12,19,24,15,19,27,24,19][(i*2+j)%8]/12); t=np.arange(int(.33*S))/S
   a=.07*(np.sin(2*np.pi*f2*t)+.3*np.sin(2*np.pi*f2*2.003*t))*np.exp(-t*12)*np.minimum(t*180,1)
   add(at+j*beat/2,a,(-1)**j*.45);add(at+j*beat/2+.22,a*.25,(-1)**(j+1)*.6)
for at in [0,2.57,5.14,7.71,13,17,21,25,29,34]:impact(at)
for at,dur in [(7.7,2.3),(15.9,1.1),(19.9,1.1),(23.9,1.1),(27.9,1.1),(32,2)]:
 t=np.arange(int(dur*S))/S;p=t/dur;n=rng.normal(size=len(t));a=.13*p**2*(n*.4+np.sin(2*np.pi*(180*t+600*t*t/dur))*.6);add(at,a)
# Absolute silence before the reveal; no reverb leaks.
mix[int(10*S):int(12.9*S)]=0
# Cinematic sustained resolution, gently decaying to black.
t=np.arange(6*S)/S
for f in [55,110,130.8128,164.8138,220]:add(34,.055*np.sin(2*np.pi*f*t)*np.exp(-t*.45)*np.minimum(t*6,1))
mix[-S:]*=np.linspace(1,0,S)[:,None]
mix=np.tanh(mix*1.15);mix*=.89/max(np.max(np.abs(mix)),.01)
with wave.open('assets/score.wav','wb') as w:w.setnchannels(2);w.setsampwidth(2);w.setframerate(S);w.writeframes((mix*32767).astype('<i2').tobytes())
