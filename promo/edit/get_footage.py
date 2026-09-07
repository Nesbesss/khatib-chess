import subprocess,re,concurrent.futures,json
from pathlib import Path
slugs=['cinematic-shot-of-a-wooden-chess-49721','wooden-chess-board-in-a-close-up-view-49710','close-look-at-a-chess-board-49716','close-shot-of-a-chessboard-with-a-probe-lens-49709','wooden-chess-arranged-in-side-by-side-shot-49723','vertical-video-of-a-chess-king-slowly-rotating-49736','chess-board-during-a-game-5339','checkmate-in-a-chess-game-seen-up-close-49897']
def get(slug):
 url='https://mixkit.co/free-stock-video/'+slug+'/';s=subprocess.check_output(['curl','-Ls',url]).decode();Path('promo/reference/'+slug+'.html').write_text(s)
 if 'for commercial or personal use' not in s:return {'slug':slug,'status':'license not verified'}
 urls=re.findall(r'https[^\s"<>]+(?:1080|hd-ready)\.mp4',s)
 if not urls:urls=re.findall(r'https[^\s"<>]+\.mp4',s)
 media=urls[0];p=Path('promo/edit/footage')/(slug+'.mp4');subprocess.run(['curl','-Lsf',media,'-o',str(p)],check=True)
 return {'slug':slug,'page':url,'media':media,'file':str(p),'license':'Mixkit Stock Video Free License'}
with concurrent.futures.ThreadPoolExecutor(max_workers=4) as ex:results=list(ex.map(get,slugs))
Path('promo/edit/sources.json').write_text(json.dumps(results,indent=2));print(json.dumps(results,indent=2))
