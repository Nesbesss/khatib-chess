"""Blender Video Sequencer master; all titles and edits remain editable."""
import bpy,sys
from pathlib import Path
P=Path(__file__).resolve().parent;D=P/'full'
preview='--preview' in sys.argv
bpy.ops.wm.read_factory_settings(app_template='Video_Editing')
s=bpy.context.scene;s.name='EDIT / KHATIB / 32 seconds'
s.render.resolution_x=1920;s.render.resolution_y=1080;s.render.resolution_percentage=100
s.render.fps=24;s.frame_start=1;s.frame_end=768
s.view_settings.view_transform='Standard';s.view_settings.look='None'
st=s.sequence_editor_create().strips
font=bpy.data.fonts.load('/System/Library/Fonts/Supplemental/Arial Bold.ttf')
def key(o,p,v,f):setattr(o,p,v);o.keyframe_insert(data_path=p,frame=f)
def clip(name,a,b,offset=0,zoom=1):
    p=P/'hero-silent.mp4' if name=='hero' else D/(name+'.mp4')
    if preview and name!='hero':
        o=st.new_image(name=name,filepath=str(D/(name+'-preview.png')),channel=1,frame_start=a)
    else:
        o=st.new_movie(name=name,filepath=str(p),channel=1,frame_start=a-offset)
        o.frame_final_start=a
    o.frame_final_end=b
    o.transform.scale_x=zoom;o.transform.scale_y=zoom
    return o
clip('hero',1,49,0)
clip('champion',49,79,28,1.35)
clip('opponents',79,103,30,1.12)
clip('evolution',103,121,35,1.25)
clip('hero',121,133,12,1.3)
clip('evolution',133,145,80,1.45)
clip('hero',193,313,0)
clip('champion',313,433)
clip('opponents',433,553)
clip('evolution',553,673)
clip('hero',673,769,24)
def fill(name,a,b,color,alpha,ch):
    o=st.new_effect(name=name,type='COLOR',channel=ch,frame_start=a,length=b-a)
    o.width=1920;o.height=1080;o.color=color;o.blend_type='ALPHA_OVER';o.blend_alpha=alpha
    return o
def text(body,a,b,size=76,pos=(.065,.67),center=False,ch=5,color=(.87,.94,.91,1),fade=8):
    o=st.new_effect(name=body.replace('\n',' '),type='TEXT',channel=ch,frame_start=a,length=b-a)
    o.text=body;o.font=font;o.font_size=size;o.color=color;o.location=pos
    o.anchor_x='CENTER' if center else 'LEFT';o.anchor_y='TOP';o.alignment_x='CENTER' if center else 'LEFT'
    o.blend_type='ALPHA_OVER';o.use_shadow=True;o.shadow_color=(0,0,0,.3)
    key(o,'blend_alpha',0,a);key(o,'blend_alpha',1,a+fade);key(o,'blend_alpha',1,b-fade-1);key(o,'blend_alpha',0,b-1)
    key(o.transform,'offset_y',-18,a);key(o.transform,'offset_y',0,a+12)
    return o
text('THINK.',10,43,142,(.5,.59),True,fade=3)
text('DEEPER.',52,77,142,(.5,.59),True,fade=3)
fill('Two seconds of absolute silence',145,193,(0,0,0),1,3)
text('ONE MOVE.',160,190,88,(.5,.56),True,fade=5)
text('CHANGES EVERYTHING.',166,190,23,(.5,.44),True,ch=6,fade=4)
text('AN UNDER-18',323,427,23,(.065,.72),ch=5,color=(.35,.94,.57,1))
text('CHAMPION.\nDEFEATED.',328,427,76,(.065,.65),ch=6)
text('MULTIPLE BOTS.',445,547,23,(.065,.90),ch=5,color=(.35,.94,.57,1))
text('BEATEN.',450,547,90,(.065,.83),ch=6)
text('STILL',565,667,25,(.065,.72),ch=5,color=(.35,.94,.57,1))
text('IMPROVING.',570,667,74,(.065,.65),ch=6)
text('THINK DEEPER.',731,763,22,(.10,.44),ch=6,fade=5)
for a in [49,79,103,121,133,193,313,433,553,673]:
    o=fill('Cut light',a,a+2,(.40,.75,.57),.075,8);key(o,'blend_alpha',.075,a);key(o,'blend_alpha',0,a+1)
o=fill('End fade',752,769,(0,0,0),0,10);key(o,'blend_alpha',0,752);key(o,'blend_alpha',1,768)
st.new_sound(name='Mastered cinematic mix',filepath=str(D/'soundtrack.wav'),channel=15,frame_start=1)
for name in ['music','effects']:
    stem=st.new_sound(name=name.title()+' / editable stem / muted',filepath=str(D/(name+'.wav')),channel=16 if name=='music' else 17,frame_start=1)
    stem.mute=True
for name,start in [('Pressure',1),('Silence',145),('Khatib',193),('Champion',313),('Bots',433),('Still improving',553),('End card',673)]:s.timeline_markers.new(name,frame=start)
if not preview:
    for name in ['champion','opponents','evolution']:
        with bpy.data.libraries.load(str(D/(name+'.blend')),link=False) as (src,dst):dst.scenes=list(src.scenes)
    with bpy.data.libraries.load(str(P/'Khatib-3D-Test.blend'),link=False) as (src,dst):dst.scenes=list(src.scenes)
    for reveal in dst.scenes:reveal.name='KHATIB / reveal'
for window in bpy.context.window_manager.windows:window.scene=s
s.frame_set(360)
for screen in bpy.data.screens:
    for area in screen.areas:
        if area.type=='SEQUENCE_EDITOR':area.spaces.active.show_seconds=True
s.render.image_settings.media_type='VIDEO';s.render.image_settings.file_format='FFMPEG'
s.render.ffmpeg.format='MPEG4';s.render.ffmpeg.codec='H264';s.render.ffmpeg.constant_rate_factor='HIGH'
s.render.ffmpeg.audio_codec='AAC';s.render.ffmpeg.audio_bitrate=320
s.render.filepath=str(D/'Khatib-Premium-Launch.mp4')
bpy.ops.file.pack_all()
if not preview:
    bpy.ops.wm.save_as_mainfile(filepath=str(D/'Khatib-Premium-Launch.blend'))
    bpy.ops.file.make_paths_relative()
    bpy.ops.wm.save_as_mainfile(filepath=str(D/'Khatib-Premium-Launch.blend'))
if preview:
    s.render.image_settings.media_type='IMAGE';s.render.image_settings.file_format='PNG';s.render.resolution_percentage=50
    for f in [174,360,475,610,738]:
        s.frame_set(f);s.render.filepath=str(D/f'edit-{f}.png');bpy.ops.render.render(write_still=True)
else:bpy.ops.render.render(animation=True)
