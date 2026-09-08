"""Quiet picture edit for evaluating pacing before the next score."""
import bpy
from pathlib import Path
P=Path(__file__).resolve().parent;D=P/'calm';D.mkdir(exist_ok=True)
bpy.ops.wm.open_mainfile(filepath=str(P/'full/Khatib-Premium-Launch.blend'))
s=bpy.data.scenes['EDIT / KHATIB / 32 seconds'];s.name='KHATIB / calm picture edit'
st=s.sequence_editor.strips
for o in list(st):st.remove(o)
for m in list(s.timeline_markers):s.timeline_markers.remove(m)
s.frame_end=576
def key(o,p,v,f):setattr(o,p,v);o.keyframe_insert(data_path=p,frame=f)
def clip(name,a,b,offset=0):
    p=P/'hero-silent.mp4' if name=='hero' else P/'full'/(name+'.mp4')
    o=st.new_movie(name=name,filepath=str(p),channel=1,frame_start=a-offset)
    o.frame_final_start=a;o.frame_final_end=b
clip('hero',1,121)
clip('champion',121,241)
clip('opponents',241,361)
clip('evolution',361,481)
clip('hero',481,577,24)
font=bpy.data.fonts.load('/System/Library/Fonts/Supplemental/Arial.ttf')
def title(body,a,b,pos=(.065,.67),size=60):
    o=st.new_effect(name=body,type='TEXT',channel=5,frame_start=a,length=b-a)
    o.text=body;o.font=font;o.font_size=size;o.color=(.86,.93,.90,1)
    o.location=pos;o.anchor_x='LEFT';o.anchor_y='TOP';o.alignment_x='LEFT';o.blend_type='ALPHA_OVER'
    key(o,'blend_alpha',0,a);key(o,'blend_alpha',1,a+16);key(o,'blend_alpha',1,b-17);key(o,'blend_alpha',0,b-1)
title('A win against an\nunder-18 champion.',137,231,size=52)
title('Multiple bots.\nDefeated.',257,351,pos=(.065,.9),size=52)
title('Still improving.',377,471,size=60)
title('Think deeper.',539,571,pos=(.10,.44),size=28)
# Restrained fade only at the ends. Sustained camera moves carry the interior edit.
for a,b,inward in [(1,17,True),(557,577,False)]:
    o=st.new_effect(name='Opening fade' if inward else 'Closing fade',type='COLOR',channel=9,frame_start=a,length=b-a)
    o.width=1920;o.height=1080;o.color=(0,0,0);o.blend_type='ALPHA_OVER'
    key(o,'blend_alpha',1 if inward else 0,a);key(o,'blend_alpha',0 if inward else 1,b-1)
for name,f in [('Reveal',1),('Champion',121),('Bots',241),('Improvement',361),('Brand',481)]:s.timeline_markers.new(name,frame=f)
for w in bpy.context.window_manager.windows:w.scene=s
s.frame_set(180);s.render.filepath=str(D/'Khatib-Pacing-Study.mp4')
bpy.ops.file.pack_all();bpy.ops.wm.save_as_mainfile(filepath=str(D/'Khatib-Pacing-Study.blend'))
bpy.ops.render.render(animation=True)
