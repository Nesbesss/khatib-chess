"""Finish full-frame overlays natively, reusing the completed 3D render."""
import bpy
from pathlib import Path
P=Path(__file__).resolve().parent
bpy.ops.wm.open_mainfile(filepath=str(P/'Khatib-Software.blend'))
master=bpy.data.scenes['EDIT / 40 second master']
for strip in master.sequence_editor.strips:
    if strip.type=='COLOR': strip.width=1920;strip.height=1080
bpy.ops.wm.save_as_mainfile(filepath=str(P/'Khatib-Software.blend'))

s=bpy.data.scenes.new('DELIVERY / full-frame compositing')
bpy.context.window.scene=s
s.render.resolution_x=1920;s.render.resolution_y=1080;s.render.resolution_percentage=100;s.render.fps=30;s.frame_start=1;s.frame_end=1200
s.view_settings.view_transform='Standard';s.view_settings.look='None'
st=s.sequence_editor_create().strips
v=st.new_movie(name='Completed native interface render',filepath=str(P/'software-render.mp4'),channel=1,frame_start=1)
v.frame_final_end=1201
st.new_sound(name='Completed mix',filepath=str(P/'software-render.mp4'),channel=20,frame_start=1)
def fr(t):return round(t*30)+1
def key(o,p,v,t):setattr(o,p,v);o.keyframe_insert(data_path=p,frame=fr(t))
def color(name,a,b,c,alpha,ch):
    o=st.new_effect(name=name,type='COLOR',channel=ch,frame_start=fr(a),length=fr(b)-fr(a));o.width=1920;o.height=1080;o.color=c;o.blend_type='ALPHA_OVER';o.blend_alpha=alpha;return o
color('Absolute black during silence',10,13,(0,0,0),1,3)
src=master.sequence_editor.strips['Then, one move.']
o=st.new_effect(name=src.name,type='TEXT',channel=4,frame_start=fr(11.2),length=fr(12.85)-fr(11.2))
for p in ['text','font','font_size','color','location','anchor_x','anchor_y','alignment_x','blend_type','use_shadow','shadow_color']:
    setattr(o,p,getattr(src,p))
key(o,'blend_alpha',0,11.2);key(o,'blend_alpha',1,11.36);key(o.transform,'offset_y',-24,11.2);key(o.transform,'offset_y',0,11.48);key(o,'blend_alpha',1,12.73);key(o,'blend_alpha',0,12.85-1/30)
for t in [0,2,3.5,5,6.5,7.5,8,8.5,9,13,17,21,25,29,34]:
    o=color('Exposure transition',t,t+2/30,(.63,.88,.47),.14,9);key(o,'blend_alpha',.14,t);key(o,'blend_alpha',0,t+1/30)
o=color('End fade',39,40,(0,0,0),0,10);key(o,'blend_alpha',0,39);key(o,'blend_alpha',1,40)
s.render.image_settings.media_type='VIDEO';s.render.image_settings.file_format='FFMPEG';s.render.ffmpeg.format='MPEG4';s.render.ffmpeg.codec='H264';s.render.ffmpeg.constant_rate_factor='HIGH';s.render.ffmpeg.ffmpeg_preset='GOOD';s.render.ffmpeg.audio_codec='AAC';s.render.ffmpeg.audio_bitrate=320;s.render.filepath=str(P/'software-finished.mp4')
bpy.ops.render.render(animation=True)
