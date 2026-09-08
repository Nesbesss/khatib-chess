"""Native Blender motion design: original software concept, real engine sample."""
import bpy
import json
import math
from pathlib import Path
from mathutils import Vector

P = Path(__file__).resolve().parent
D = json.loads((P/'assets/analysis.json').read_text())
FPS = 30
def fr(t): return round(t*FPS)+1
def k(o, prop, value, t):
    setattr(o, prop, value)
    o.keyframe_insert(data_path=prop, frame=fr(t))

bpy.ops.wm.read_factory_settings(use_empty=False)
for ob in list(bpy.data.objects): bpy.data.objects.remove(ob, do_unlink=True)
s = bpy.context.scene
s.name = 'KHATIB / Software launch'
s.render.engine = 'BLENDER_EEVEE'
s.render.resolution_x=1920; s.render.resolution_y=1080
s.render.resolution_percentage=100; s.render.fps=FPS
s.frame_start=1; s.frame_end=1200
s.view_settings.view_transform='Standard'; s.view_settings.look='None'
s.world.color=(.002,.003,.002)
s.render.image_settings.color_mode='RGB'
s.render.use_compositing=False
s.render.use_motion_blur=True
s.render.motion_blur_shutter=.35
if hasattr(s,'eevee'):
    s.eevee.taa_render_samples=16

def rgba(h):
    rgb=[int(h[i:i+2],16)/255 for i in (0,2,4)]
    return tuple(v/12.92 if v<.04045 else ((v+.055)/1.055)**2.4 for v in rgb)+(1,)
def mat(name,h):
    m=bpy.data.materials.new(name);m.diffuse_color=rgba(h);m.use_nodes=True
    n=m.node_tree.nodes;n.clear();e=n.new('ShaderNodeEmission');e.inputs[0].default_value=rgba(h)
    out=n.new('ShaderNodeOutputMaterial');m.node_tree.links.new(e.outputs[0],out.inputs[0]);return m
M={n:mat(n,h) for n,h in {'bg':'070D0D','panel':'111D1E','edge':'273B3A','inner':'172828','white':'F1F3E8','dim':'78908A','mint':'B7F971','dark':'45655B','light':'CCDBBD','line':'14231F','blue':'72CFD4'}.items()}
font=bpy.data.fonts.load('/System/Library/Fonts/HelveticaNeue.ttc')
bold=bpy.data.fonts.load('/System/Library/Fonts/Supplemental/Arial Bold.ttf')
mono=bpy.data.fonts.load('/System/Library/Fonts/Menlo.ttc')

def group(name,parent=None):
    o=bpy.data.objects.new(name,None);s.collection.objects.link(o);o.parent=parent;return o
def rect(name,w,h,x,y,z,material,parent=None,r=.12):
    r=min(r,w/2,h/2);v=[]
    for cx,cy,a in [(w/2-r,h/2-r,0),(-w/2+r,h/2-r,90),(-w/2+r,-h/2+r,180),(w/2-r,-h/2+r,270)]:
        for j in range(9):
            t=math.radians(a+j*90/8);v.append((cx+r*math.cos(t),cy+r*math.sin(t),0))
    mesh=bpy.data.meshes.new(name);mesh.from_pydata(v,[],[list(range(len(v)))]);mesh.materials.append(material)
    o=bpy.data.objects.new(name,mesh);s.collection.objects.link(o);o.location=(x,y,z);o.parent=parent;return o
def line(name,points,material,width=.012,parent=None):
    c=bpy.data.curves.new(name,'CURVE');c.dimensions='3D';c.bevel_depth=width;c.bevel_resolution=2
    sp=c.splines.new('POLY');sp.points.add(len(points)-1)
    for p,co in zip(sp.points,points):p.co=(*co,1)
    c.materials.append(material);o=bpy.data.objects.new(name,c);s.collection.objects.link(o);o.parent=parent;return o
def txt(name,body,size,x,y,z=1,material=None,parent=None,boldface=False):
    c=bpy.data.curves.new(name,'FONT');c.body=body;c.size=size;c.font=bold if boldface else font;c.align_y='CENTER';c.space_character=1.05;c.materials.append(material or M['white'])
    o=bpy.data.objects.new(name,c);s.collection.objects.link(o);o.location=(x,y,z);o.parent=parent;return o
def card(name,w,h,x,y,parent):
    rect(name+' / border',w+.025,h+.025,x,y,0,M['edge'],parent,.2)
    return rect(name,w,h,x,y,.015,M['panel'],parent,.19)

# Depth in these native meshes creates the parallax during the camera moves.
rect('Infinite dark canvas',100,100,0,0,-2,M['bg'],r=0)
for i in range(-20,21):
    line('Signal grid horizontal '+str(i),[(-22,i*.65,-1.8),(22,i*.65,-1.8)],M['line'],.002)
    line('Signal grid vertical '+str(i),[(i*.65,-15,-1.8),(i*.65,15,-1.8)],M['line'],.002)
for i in range(42):
    x=math.sin(i*7.31)*12;y=math.cos(i*4.71)*6
    o=rect('Ambient signal '+str(i),.018,.018,x,y,-1.6,M['mint'],r=.008)
    k(o,'location',(x,y,-1.6),0);k(o,'location',(x+.5,y+.6,-1.6),40)

dashboard=group('INTERFACE / original promotional design')
board=group('DIGITAL BOARD',dashboard)
board.location=(-2.15,0,0)
card('Board surface',5.8,6.65,0,0,board)
txt('Board brand','K H A T I B',.22,-2.45,2.93,.2,M['white'],board,True)
rect('Status dot',.055,.055,1.3,2.93,.2,M['mint'],board)
txt('Analysis state','ANALYSIS',.13,1.47,2.93,.2,M['dim'],board)
step=.6
for rank in range(8):
    for file in range(8):
        rect(f'Square {chr(97+file)}{rank+1}',step-.006,step-.006,(file-3.5)*step,(rank-3.5)*step,.12,M['light' if (rank+file)%2 else 'dark'],board,r=.015)
for i in range(8):
    txt('File coordinate '+str(i),chr(97+i),.12,(i-3.5)*step-.035,-2.6,.15,M['dim'],board)
    txt('Rank coordinate '+str(i),str(i+1),.12,-2.64,(i-3.5)*step,.15,M['dim'],board)
txt('Sample label','PRINCIPAL VARIATION  /  DEMO POSITION',.12,-2.43,-2.97,.2,M['dim'],board)

# Cburnett vector piece artwork, rendered as transparent textures, not photographs.
piece_mats={}
for sym in 'PNBRQKpnbrqk':
    fn=('white-' if sym.isupper() else 'black-')+sym.lower()+'.png'
    m=bpy.data.materials.new('Vector piece '+sym);m.use_nodes=True
    ns=m.node_tree.nodes;ns.clear();image=ns.new('ShaderNodeTexImage');image.image=bpy.data.images.load(str(P/'assets'/fn));image.interpolation='Linear'
    em=ns.new('ShaderNodeEmission');tr=ns.new('ShaderNodeBsdfTransparent');mix=ns.new('ShaderNodeMixShader');out=ns.new('ShaderNodeOutputMaterial')
    m.node_tree.links.new(image.outputs['Color'],em.inputs[0]);m.node_tree.links.new(image.outputs['Alpha'],mix.inputs[0]);m.node_tree.links.new(tr.outputs[0],mix.inputs[1]);m.node_tree.links.new(em.outputs[0],mix.inputs[2]);m.node_tree.links.new(mix.outputs[0],out.inputs[0])
    m.surface_render_method='DITHERED';piece_mats[sym]=m
def icon(sym,file,rank):
    mesh=bpy.data.meshes.new('Piece image');mesh.from_pydata([(-.27,-.27,0),(.27,-.27,0),(.27,.27,0),(-.27,.27,0)],[],[(0,1,2,3)])
    uv=mesh.uv_layers.new()
    for i,p in enumerate([(0,0),(1,0),(1,1),(0,1)]):uv.data[i].uv=p
    mesh.materials.append(piece_mats[sym]);o=bpy.data.objects.new('Piece '+sym+' '+chr(97+file)+str(rank+1),mesh);s.collection.objects.link(o);o.parent=board;o.location=((file-3.5)*step,(rank-3.5)*step,.3);return o
pieces={}
for row,part in enumerate(D['fen'].split()[0].split('/')):
    col=0
    for ch in part:
        if ch.isdigit():col+=int(ch)
        else:pieces[chr(97+col)+str(8-row)]=icon(ch,col,7-row);col+=1
def pos(sq):return ((ord(sq[0])-97-3.5)*step,(int(sq[1])-1-3.5)*step,.34)
# Replay the captured line across the different product shots.
initial=pieces.copy()
for cycle in [0,13,21,29,36]:
    pieces=initial.copy()
    for sq,obj in pieces.items():
        if cycle:
            k(obj,'location',obj.location.copy(),cycle-.05)
            k(obj,'scale',obj.scale.copy(),cycle-.05)
        k(obj,'location',pos(sq),cycle);k(obj,'scale',(1,1,1),cycle)
    for i,uci in enumerate(D['sample']['pv'][:8 if cycle<36 else 3]):
        source,target=uci[:2],uci[2:4]
        if source not in pieces:break
        obj=pieces.pop(source);t=cycle+1+i*.83
        k(obj,'location',pos(source),t);k(obj,'location',pos(target),t+.28)
        if target in pieces:
            cap=pieces.pop(target);k(cap,'scale',(1,1,1),t+.17);k(cap,'scale',(0,0,0),t+.3)
        pieces[target]=obj
        trail=line('PV trail '+str(cycle)+' '+uci,[pos(source),pos(target)],M['mint'],.016,board)
        k(trail,'scale',(0,0,0),0);k(trail,'scale',(0,0,0),t);k(trail,'scale',(1,1,1),t+.13);k(trail,'scale',(1,1,1),t+.55);k(trail,'scale',(0,0,0),t+.7)

side=group('SEARCH / real engine sample',dashboard);side.location=(2.8,0,.12)
card('Search surface',3.65,6.65,0,0,side)
txt('Search heading','Inside the search.',.28,-1.48,2.86,.2,M['white'],side,True)
txt('Search subheading','Candidate moves. One best line.',.135,-1.48,2.5,.2,M['dim'],side)
rect('Search highlight',3.02,.83,0,1.71,.12,M['inner'],side)
txt('Search label','ROOT SEARCH EVALUATION',.13,-1.29,1.89,.2,M['dim'],side)
score=D['sample']['score'];value=score.get('value',0)
scorestr=f'{value/100:+.2f}' if score.get('type')=='cp' else '#'+str(value)
txt('Actual eval',scorestr,.43,-1.29,1.5,.2,M['mint'],side,True)
txt('Eval perspective','side to move',.12,.28,1.5,.2,M['dim'],side)
txt('PV heading','PRINCIPAL VARIATION',.12,-1.45,.89,.2,M['dim'],side)
for i,move in enumerate(D['san'][:6]):
    y=.47-i*.43
    rect('Move tile '+str(i),.25,.25,-1.27,y,.19,M['inner'],side,.06)
    txt('Move index '+str(i),str(i+1),.12,-1.31,y,.24,M['mint'],side)
    txt('Move '+str(i),move,.21,-.96,y,.22,M['white'],side)
    bar=rect('Search pulse '+str(i),.75-.07*i,.025,.83,y,.22,M['mint'] if i==0 else M['edge'],side,.01)
    for tt,scale in [(0,.2),(1+i*.8,1),(8,.45),(13,1),(21,.55),(25,1),(29,.7),(40,.7)]:k(bar,'scale',(scale,1,1),tt)
txt('Engine architecture','NNUE  /  RUST  /  UCI',.14,-1.45,-2.45,.2,M['white'],side)
txt('Source note','Khatib analysis capture',.12,-1.45,-2.9,.2,M['dim'],side)

# Small signal diagrams provide a software visual language around the interface.
network=group('NEURAL SIGNALS / conceptual animation')
for col,count in enumerate([5,7,7,5]):
    for row in range(count):
        x=col*.55-1;y=(row-(count-1)/2)*.35
        rect('Signal node',.045,.045,x,y,-.4,M['mint'],network,.02)
        if col<3:
            nxt=[5,7,7,5][col+1]
            for n in range(nxt):
                if (row+n)%3==0:line('Neural connection',[(x,y,-.43),(x+.55,(n-(nxt-1)/2)*.35,-.43)],M['edge'],.004,network)
network.location=(-4.5,-1.9,0)

# Per-shot transforms are editable keyframes, with camera parallax and staged assembly.
for t,loc,sc,rot in [
    (0,(0,0,0),1,0),(9.9,(0,0,0),1,0),
    (12.99,(8,-1,0),.68,-.11),(13.55,(3.65,0,0),.70,0),
    (16.8,(3.65,0,0),.70,0),(17.4,(-3.5,0,0),.76,.025),
    (20.8,(-3.5,0,0),.76,.025),(21.4,(3.65,0,0),.70,0),
    (24.7,(3.65,0,0),.70,0),(25.4,(0,-.55,0),.97,0),
    (28.8,(0,-.55,0),.97,0),(29.4,(3.65,0,0),.70,0),
    (33.7,(3.65,0,0),.70,0),(34.4,(0,-1.45,0),.60,0),(40,(0,-1.45,0),.60,0)]:
    k(dashboard,'location',loc,t);k(dashboard,'scale',(sc,sc,sc),t);k(dashboard,'rotation_euler',(0,rot,rot*.3),t)
for tt,sc in [(0,0),(13,0),(13.7,1),(16.8,1),(17.2,0),(21,0),(21.5,1),(24.8,1),(25.2,0),(29.1,0),(29.6,1),(33.9,1),(34.2,0)]:k(network,'scale',(sc,sc,sc),tt)

camdata=bpy.data.cameras.new('Motion camera');cam=bpy.data.objects.new('Motion camera',camdata);s.collection.objects.link(cam);s.camera=cam;camdata.type='ORTHO';camdata.ortho_scale=16;cam.location=(0,0,25);cam.rotation_euler=(0,0,0)
for tt,xy,scale,roll in [(0,(-2.2,0),7,.08),(1.8,(-2.2,.4),6.6,.04),(2, (2.75,1.3),4.4,-.06),(3.4,(2.8,.2),4.8,0),(3.5,(-2.2,.2),7.5,-.12),(5,(-2,0),6.3,.04),(6.5,(2.7,-.6),4.7,.04),(7.5,(-2,0),7,-.05),(8,(2.7,.3),4.3,0),(8.5,(-2,0),6.3,.1),(9,(2.8,1.4),3.9,-.03),(9.5,(-2,0),5.5,.1),(10,(0,0),16,0),(12.9,(0,0),16,0),(40,(0,0),16,0)]:
    k(cam,'location',(xy[0],xy[1],25),tt);k(camdata,'ortho_scale',scale,tt);k(cam,'rotation_euler',(0,0,roll),tt)

# Native sequence typography is independent of the camera and stays readable.
ed=s.sequence_editor_create();st=ed.strips
def title(body,a,b,size=85,x=.075,y=.58,color='white',ch=4,align='LEFT',weight=True):
    o=st.new_effect(name=body,type='TEXT',channel=ch,frame_start=fr(a),length=fr(b)-fr(a));o.text=body;o.font=bold if weight else font;o.font_size=size;o.color=tuple(int({'white':'F1F3E8','mint':'B7F971','dim':'91ABA2'}[color][i:i+2],16)/255 for i in (0,2,4))+(1,);o.location=(x,y);o.anchor_x=align;o.anchor_y='CENTER';o.alignment_x=align;o.blend_type='ALPHA_OVER';o.use_shadow=True;o.shadow_color=(0,0,0,.45)
    k(o,'blend_alpha',0,a);k(o,'blend_alpha',1,a+.16);k(o.transform,'offset_y',-24,a);k(o.transform,'offset_y',0,a+.28)
    k(o,'blend_alpha',1,b-.12);k(o,'blend_alpha',0,b-1/FPS)
    return o
def fill(name,a,b,color=(0,0,0),alpha=1,ch=3):
    o=st.new_effect(name=name,type='COLOR',channel=ch,frame_start=fr(a),length=fr(b)-fr(a));o.width=1920;o.height=1080;o.color=color;o.blend_type='ALPHA_OVER';o.blend_alpha=alpha;return o
# The scene is rendered as a distinct scene strip, keeping the complete motion scene editable.
motion=s
master=bpy.data.scenes.new('EDIT / 40 second master')
master.render.resolution_x=1920;master.render.resolution_y=1080;master.render.resolution_percentage=100;master.render.fps=30;master.frame_start=1;master.frame_end=1200
master.view_settings.view_transform='Standard';master.view_settings.look='None'
base=master.sequence_editor_create().strips.new_scene(name='KHATIB / native animated interface',scene=motion,channel=1,frame_start=1)
base.scene_input='CAMERA'
# Build titles in the master, so scene geometry remains a separately inspectable asset.
st=master.sequence_editor.strips
fill('Silence / black',10,13)
title('ONE ENGINE.',.3,1.8,135,.5,.51,align='CENTER')
title('EVERY POSSIBILITY.',2.15,3.35,104,.5,.51,align='CENTER')
title('SEARCH.',3.6,4.6,160,.5,.51,'mint',align='CENTER')
title('EVALUATE.',5.1,6.35,135,.5,.51,align='CENTER')
title('OUTTHINK.',6.65,7.45,145,.5,.51,'mint',align='CENTER')
for body,tt in [('SEARCH',7.55),('EVALUATE',8.05),('SELECT',8.55),('MOVE',9.05)]:title(body,tt,tt+.4,130,.5,.51,align='CENTER')
title('Then, one move.',11.2,12.85,43,.5,.5,align='CENTER',weight=False)
title('MEET THE ENGINE',13.2,16.8,22,y=.69,color='mint',weight=False)
title('Khatib.',13.32,16.9,154,y=.55)
title('Chess intelligence.\nBuilt in software.',13.6,16.9,39,y=.39,weight=False)
title('AN UNDER-18 CHAMPION',17.3,20.9,27,x=.61,y=.63,color='dim',weight=False)
title('Beaten.',17.55,20.9,132,x=.61,y=.49,color='mint')
title('THE RESULTS',21.3,24.9,23,y=.67,color='mint',weight=False)
title('Bots.\nBeaten.',21.5,24.9,108,y=.5)
title('Opponent after opponent.',22,24.9,28,y=.29,weight=False)
title('Neural evaluation. Relentless search.',25.3,28.9,54,.5,.90,align='CENTER')
title('STILL IN DEVELOPMENT',29.3,33.8,23,y=.68,color='mint',weight=False)
title('Still\nimproving.',29.55,33.9,94,y=.49)
title('Self-play. Train. Test. Repeat.',30.2,33.9,28,y=.28,weight=False)
title('Khatib.',34.4,40,105,.5,.83,align='CENTER')
title('Your move.',34.7,40,33,.5,.72,'mint',align='CENTER',weight=False)
title('github.com/Nesbesss/khatib-chess',35.4,40,23,.5,.065,'dim',align='CENTER',weight=False)
for tt in [0,2,3.5,5,6.5,7.5,8,8.5,9,13,17,21,25,29,34]:
    o=fill('Cut exposure',tt,tt+2/FPS,(.63,.88,.47),.14,9);k(o,'blend_alpha',.14,tt);k(o,'blend_alpha',0,tt+1/FPS)
o=fill('Fade to black',39,40,(0,0,0),0,10);k(o,'blend_alpha',0,39);k(o,'blend_alpha',1,40)
for name,filename,ch in [('MUSIC / Minimal Emotion','music-bed.wav',20),('SOUND DESIGN / recorded Mixkit effects','effects-bed.wav',21)]:
    st.new_sound(name=name,filepath=str(P.parent/'assets'/filename),channel=ch,frame_start=1)
for t,name in [(0,'01 / IN THE SEARCH'),(10,'02 / SILENCE'),(13,'03 / SOFTWARE REVEAL'),(17,'04 / CHAMPION WIN'),(21,'05 / BOTS'),(25,'06 / NEURAL EVALUATION'),(29,'07 / DEVELOPMENT'),(34,'08 / KHATIB')]:master.timeline_markers.new(name,frame=fr(t))
master.render.image_settings.media_type='VIDEO';master.render.image_settings.file_format='FFMPEG';master.render.ffmpeg.format='MPEG4';master.render.ffmpeg.codec='H264';master.render.ffmpeg.constant_rate_factor='HIGH';master.render.ffmpeg.ffmpeg_preset='GOOD';master.render.ffmpeg.audio_codec='AAC';master.render.ffmpeg.audio_bitrate=320;master.render.filepath=str(P/'software-render.mp4')
bpy.context.window.scene=master
master.frame_set(fr(14.5))
for screen in bpy.data.screens:
    for area in screen.areas:
        if area.type=='VIEW_3D':
            area.type='SEQUENCE_EDITOR'
            for space in area.spaces:
                if space.type=='SEQUENCE_EDITOR':space.view_type='SEQUENCER_PREVIEW'
        if area.type=='DOPESHEET_EDITOR':
            area.ui_type='TIMELINE'
bpy.ops.file.pack_all()
bpy.ops.wm.save_as_mainfile(filepath=str(P/'Khatib-Software.blend'))
master.render.image_settings.media_type='IMAGE';master.render.image_settings.file_format='PNG';master.render.resolution_percentage=50
for t in [1,3,6,14.5,18.5,23,27,31,36]:
    master.frame_set(fr(t));master.render.filepath=str(P/f'preview-{t}.png');bpy.ops.render.render(write_still=True)
print('SOFTWARE MOTION PROJECT READY')
