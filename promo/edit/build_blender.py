import bpy,json,math
from pathlib import Path
P=Path(__file__).resolve().parent;FPS=30
bpy.ops.wm.read_factory_settings(use_empty=True);s=bpy.context.scene;s.render.resolution_x=1920;s.render.resolution_y=1080;s.render.resolution_percentage=100;s.render.fps=FPS;s.frame_start=1;s.frame_end=1200;s.render.engine='BLENDER_EEVEE';s.view_settings.view_transform='Standard';s.view_settings.look='None';s.render.film_transparent=False
ed=s.sequence_editor_create();strips=ed.strips
impact=bpy.data.fonts.load('/System/Library/Fonts/Supplemental/Impact.ttf');sans=bpy.data.fonts.load('/System/Library/Fonts/HelveticaNeue.ttc')
def frame(t):return 1+round(t*FPS)
def key(obj,prop,v,t):setattr(obj,prop,v);obj.keyframe_insert(data_path=prop,frame=frame(t))
def color(name,a,b,c,channel,alpha=1):
 x=strips.new_effect(name=name,type='COLOR',channel=channel,frame_start=frame(a),length=frame(b)-frame(a));x.color=c;x.blend_type='ALPHA_OVER';x.blend_alpha=alpha;return x
color('BLACK BASE',0,40,(.004,.006,.005),1)
for i,d in enumerate(json.loads((P/'edit.json').read_text())):
 a,b=d['start'],d['end'];x=strips.new_movie(name=f'{i+1:02} | CHESS / {d["speed"]}x',filepath=d['file'],channel=2,frame_start=frame(a));x.frame_final_end=frame(b);x.blend_type='ALPHA_OVER';x.color_tag='COLOR_04';
 # Optical punch-in settling into a slow push, keyed per shot.
 tr=x.transform;scale=1.07 if b-a<1 else 1.015
 key(tr,'scale_x',scale+.075,a);key(tr,'scale_y',scale+.075,a);key(tr,'scale_x',scale,a+.16);key(tr,'scale_y',scale,a+.16);key(tr,'scale_x',scale+.025,b-1/FPS);key(tr,'scale_y',scale+.025,b-1/FPS)
 if a>=34:key(x,'blend_alpha',1,38.7);key(x,'blend_alpha',0,40)
# Cinematic wide matte, built as native strips.
for name,y in [('TOP MATTE',501),('BOTTOM MATTE',-501)]:
 x=color(name,0,40,(0,0,0),12 if y>0 else 13);x.transform.scale_y=78/1080;x.transform.offset_y=y
# Subtle exposure dip beneath the main text to preserve footage contrast.
for a,b,opacity in [(0,2,.22),(5,7,.22),(13,17,.18),(17,21,.23),(21,25,.22),(25,29,.22),(29,34,.18),(34,40,.3)]:color('TEXT EXPOSURE',a,b,(0,0,0),3,opacity)
def text(body,a,b,size=170,y=.5,col=(.96,.98,.94,1),font=impact,channel=6,anim=True):
 x=strips.new_effect(name=body.replace('\n',' / '),type='TEXT',channel=channel,frame_start=frame(a),length=frame(b)-frame(a));x.text=body;x.font=font;x.font_size=size;x.color=col;x.location=(.5,y);x.anchor_x='CENTER';x.anchor_y='CENTER';x.alignment_x='CENTER';x.blend_type='ALPHA_OVER';x.use_shadow=True;x.shadow_color=(0,0,0,.55);x.shadow_blur=.5;x.shadow_offset=.012;x.color_tag='COLOR_03'
 if anim:
  key(x.transform,'scale_x',1.22,a);key(x.transform,'scale_y',1.22,a);key(x.transform,'scale_x',1,a+.17);key(x.transform,'scale_y',1,a+.17)
  key(x.transform,'offset_y',-48,a);key(x.transform,'offset_y',0,a+.17);key(x,'blend_alpha',0,a);key(x,'blend_alpha',1,a+.1)
 return x
lime=(.69,1,.32,1)
text('64 SQUARES.',.15,1.9,175)
text('THINK.',2.5,3.4,210)
text('FASTER.',3.5,4.4,210,col=lime)
text('SEE',5,5.9,230)
text('FURTHER.',6,6.9,210,col=lime)
for word,a in [('SEARCH.',7.5),('ADAPT.',8),('ATTACK.',8.5),('AGAIN.',9)]:text(word,a,a+.45,200)
text('ONE MORE MOVE.',9.5,10,170)
text('Then everything changes.',11,12.8,44,font=sans,anim=False)
text('KHATIB',13,16.9,250,y=.40)
text('A  N E U R A L  C H E S S  E N G I N E',13.5,16.9,27,y=.22,font=sans,channel=7)
text('AN UNDER-18',17.1,18.35,155,y=.56)
text('CHAMPION.',18.4,19.35,175)
text('BEATEN.',19.4,21,230,col=lime)
text('BOTS.',21.1,22,230)
text('BEATEN.',22.1,23,230,col=lime)
text('AGAIN.',23.1,24,230)
text('AND AGAIN.',24.1,25,185,col=lime)
text('SELF-PLAY.',25,26,190)
text('TRAIN.',26,27,230,col=lime)
text('TEST.',27,28,230)
text('REPEAT.',28,29,230,col=lime)
text('STILL',29.1,30.4,220)
text('IMPROVING.',30.5,32,185,col=lime)
text('THIS IS ONLY',32,33,150)
text('THE OPENING.',33,34,170,col=lime)
x=text('KHATIB',34,40,215,y=.53);key(x,'blend_alpha',1,38.7);key(x,'blend_alpha',0,40)
x=text('YOUR MOVE.',34.5,40,46,y=.34,font=sans,channel=7);key(x,'blend_alpha',1,38.7);key(x,'blend_alpha',0,40)
x=text('github.com/Nesbesss/khatib-chess',35.2,40,23,y=.19,font=sans,channel=8);key(x,'blend_alpha',1,38.7);key(x,'blend_alpha',0,40)
# Two-frame exposure hits are motivated by recorded transition sounds.
for a in [2.5,5,7.5,13,17,21,25,29,34]:
 x=color('EXPOSURE HIT',a,a+2/FPS,(.8,.94,.66),10,.28);key(x,'blend_alpha',.28,a);key(x,'blend_alpha',0,a+1/FPS)
for a in [8.5,9,9.5]:color('SHUTTER CUT',a,a+1/FPS,(0,0,0),11)
# Native audio strips with independent editable music / SFX stems.
for name,filename,ch in [('MUSIC — Minimal Emotion / Mixkit','music-bed.wav',20),('SFX — Mixkit impacts, whooshes, riser','effects-bed.wav',21)]:
 path=P.parent/'assets'/filename
 if path.exists():x=strips.new_sound(name=name,filepath=str(path),channel=ch,frame_start=1)
# Markers make the editing beats explicit.
for t,name in [(0,'01 / ACCELERATE'),(10,'02 / ABSOLUTE SILENCE'),(13,'03 / KHATIB REVEAL'),(17,'04 / CHAMPION'),(21,'05 / BOTS'),(25,'06 / TRAINING'),(29,'07 / STILL IMPROVING'),(34,'08 / YOUR MOVE')]:s.timeline_markers.new(name,frame=frame(t))
s.render.image_settings.media_type='VIDEO';s.render.image_settings.file_format='FFMPEG';s.render.ffmpeg.format='MPEG4';s.render.ffmpeg.codec='H264';s.render.ffmpeg.constant_rate_factor='HIGH';s.render.ffmpeg.ffmpeg_preset='GOOD';s.render.ffmpeg.audio_codec='AAC';s.render.ffmpeg.audio_bitrate=320;s.render.filepath=str(P/'khatib-promo-v2.mp4')
# Open as a real video-editing workspace, with the sequence timeline visible.
for screen in bpy.data.screens:
 for area in screen.areas:
  if area.type=='VIEW_3D':area.type='SEQUENCE_EDITOR'
  elif area.type=='DOPESHEET_EDITOR':area.type='SEQUENCE_EDITOR'
s.frame_set(frame(14));bpy.ops.wm.save_as_mainfile(filepath=str(P/'Khatib-Promo.blend'))
# Check rendered samples before the final export.
s.render.image_settings.media_type='IMAGE';s.render.image_settings.file_format='PNG';s.render.resolution_percentage=50
for t in [1,6.5,11.5,14,19.8,24.5,31,36]:
 s.frame_set(frame(t));s.render.filepath=str(P/f'preview-{t}.png');bpy.ops.render.render(write_still=True)
print('BLENDER PROJECT AND PREVIEWS READY')
