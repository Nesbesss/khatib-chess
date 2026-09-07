import subprocess,json,concurrent.futures
from pathlib import Path
root=Path(__file__).resolve().parent; sources=json.loads((root/'sources.json').read_text());out=root/'shots';out.mkdir(exist_ok=True)
# Timeline cuts: [in, out, source, source-in, speed]. All in seconds.
shots=[(0,1,3,0,1.5),(1,2,0,1,1.5),(2,2.5,6,1.2,2),(2.5,3.5,2,2,1.8),(3.5,4,4,1,2),(4,4.5,3,4,2),(4.5,5,6,2,2),(5,6,1,2,1.8),(6,6.5,4,3,2),(6.5,7,7,1,2),(7,7.5,2,4,2),(7.5,8,3,2,2.5),(8,8.5,0,4,2.5),(8.5,8.75,6,3,2),(8.75,9,4,2,2),(9,9.25,2,3,2),(9.25,9.5,1,4,2),(9.5,9.75,7,2,2),(9.75,10,3,5,2),
(13,15,5,1,1),(15,17,3,1,1),(17,18,7,1,1.5),(18,19,6,1.5,1.5),(19,21,7,3,1),(21,21.5,4,1,1.5),(21.5,22,3,3,2),(22,22.5,2,1,2),(22.5,23,6,2,1.5),(23,24,1,2,1.5),(24,25,4,4,1.5),
(25,26,3,1,2),(26,27,2,3,2),(27,28,0,2,2),(28,29,4,3,2),(29,30.5,6,1,1),(30.5,31.5,3,4,1.5),(31.5,32,2,2,2),(32,32.5,4,4,2),(32.5,33,1,1,2),(33,33.5,0,1,2),(33.5,34,7,2,2),(34,40,5,0,1)]
def make(item):
 i,(a,b,src,trim,speed)=item;path=root.parent.parent/ sources[src]['file'];path=Path('/Users/nesbes/chess')/sources[src]['file'];target=out/f'{i:02d}.mp4';duration=b-a
 if target.exists() and src!=5:return {'file':str(target),'start':a,'end':b,'source':src,'speed':speed}
 # Real footage, time remapped and graded; sparse motion trails on the frantic opening.
 vf=f'setpts=(PTS-STARTPTS)/{speed},scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080,fps=30,setsar=1,eq=contrast=1.16:brightness=-0.015:saturation=0.52,colorbalance=rs=-0.04:gs=0.035:bs=0.01:rm=-0.035:gm=0.02:bm=0.025'
 if a>=7.5 and a<10:vf+=',tmix=frames=3:weights=1 2 4'
 if src==5:vf=vf.replace('scale=1920:1080:force_original_aspect_ratio=increase,crop=1920:1080','scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2')
 vf+=',vignette=PI/5,noise=alls=3:allf=t'
 subprocess.run(['ffmpeg','-y','-v','error','-ss',str(trim),'-i',str(path),'-vf',vf,'-t',str(duration),'-an','-c:v','libx264','-preset','fast','-crf','18','-threads','2',str(target)],check=True)
 return {'file':str(target),'start':a,'end':b,'source':src,'speed':speed}
with concurrent.futures.ThreadPoolExecutor(max_workers=3) as ex:
 results=[]
 for x in ex.map(make,enumerate(shots)):results.append(x);print('prepared',len(results),flush=True)
(root/'edit.json').write_text(json.dumps(results,indent=2))
