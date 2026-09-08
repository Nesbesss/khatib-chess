"""Native Blender shots extending the approved Khatib look."""
import bpy, math
from pathlib import Path
from mathutils import Vector
P=Path(__file__).resolve().parent
D=P/'full';D.mkdir(exist_ok=True)
def key(o,p,v,f):
    setattr(o,p,v);o.keyframe_insert(data_path=p,frame=f)
def camera(cam,focus,poses):
    for f,loc,target in poses:
        key(cam,'location',loc,f)
        key(cam,'rotation_euler',(Vector(target)-Vector(loc)).to_track_quat('-Z','Y').to_euler(),f)
        key(focus,'location',target,f)
def line(scene,name,pts,width=.012):
    c=bpy.data.curves.new(name,'CURVE');c.dimensions='3D';c.bevel_depth=width;c.bevel_resolution=3
    sp=c.splines.new('POLY');sp.points.add(len(pts)-1)
    for p,co in zip(sp.points,pts):p.co=(*co,1)
    c.materials.append(bpy.data.materials['Mint light'])
    o=bpy.data.objects.new(name,c);scene.collection.objects.link(o);return o
for kind in ['champion','opponents','evolution']:
    bpy.ops.wm.open_mainfile(filepath=str(P/'Khatib-3D-Test.blend'))
    s=bpy.context.scene;s.name='KHATIB / '+kind;s.frame_set(105)
    for o in list(s.objects):
        if o.type=='FONT':bpy.data.objects.remove(o,do_unlink=True);continue
        # Keep the evaluated approved pose, then give each new shot its own animation.
        loc=o.location.copy();rot=o.rotation_euler.copy();scale=o.scale.copy()
        o.animation_data_clear();o.location=loc;o.rotation_euler=rot;o.scale=scale
        if o.data and hasattr(o.data,'animation_data_clear'):o.data.animation_data_clear()
    root=bpy.data.objects['Floating computational board'];cam=s.camera
    focus=bpy.data.objects['Rack focus target'];cam.data.lens=48;cam.data.dof.aperture_fstop=5.6
    s.render.use_motion_blur=False;s.eevee.taa_render_samples=32
    s.frame_start=1;s.frame_end=120;s.render.resolution_percentage=100
    if kind=='champion':
        camera(cam,focus,[(1,(6.2,-8.5,6.8),(0,0,.4)),(120,(4.7,-7.2,7.7),(0,0,.55))])
        queen=bpy.data.objects['Holographic white-q']
        key(queen,'location',(.31,-1.57,.28),1);key(queen,'location',(.31,-1.57,.28),27)
        key(queen,'location',(.31,-.31,.65),43);key(queen,'location',(.31,.95,.28),61)
        key(queen,'location',(.31,.95,.28),120)
        route=line(s,'Decisive calculation',[(.31,-1.57,.32),(.31,.95,.32),(.95,1.57,.32)],.023);route.parent=root
        key(route.data,'bevel_factor_end',0,1);key(route.data,'bevel_factor_end',1,64)
        king=bpy.data.objects['Holographic black-k']
        key(king,'scale',(1,1,1),62);key(king,'scale',(0,0,0),82)
    elif kind=='opponents':
        # Three complete computational boards in perspective, with linked geometry.
        root.location=(2.4,0,.55)
        children=list(root.children)
        for n,offset in enumerate([(-2.4,2.8,.3),(2.8,5.5,.6)]):
            r=bpy.data.objects.new('Opponent board '+str(n+1),None);s.collection.objects.link(r)
            r.location=offset;r.scale=(.68,.68,.68)
            for source in children:
                o=source.copy();s.collection.objects.link(o);o.parent=r
            key(r,'location',(offset[0],offset[1],offset[2]+1.4),1)
            key(r,'location',offset,45+n*15);key(r,'rotation_euler',(0,0,-.12),1);key(r,'rotation_euler',(0,0,.12),120)
        camera(cam,focus,[(1,(8,-12,11),(0,1.7,.6)),(120,(6.5,-10.5,11.5),(0,1.7,.6))])
        for i in range(4):
            route=line(s,'Parallel search '+str(i),[(-2+i*.35,2.7,.7),(-2+i*.35,.8,.7),(2+i*.35,.8,.7)],.018)
            key(route.data,'bevel_factor_end',0,1+i*8);key(route.data,'bevel_factor_end',1,65+i*8)
    else:
        camera(cam,focus,[(1,(5.9,-8.8,7.5),(0,0,.6)),(120,(7.2,-7.4,8.8),(0,0,1))])
        for o in root.children:
            if o.name.startswith('Glass square'):
                x,y,z=o.location
                key(o,'location',(x,y,z),1)
                key(o,'location',(x,y,z+.6+.28*math.sin(x*2+y)),65)
                key(o,'location',(x,y,z+.9+.32*math.sin(x*2+y)),120)
                if int((x+2.3)/.63)%2==0:
                    p=line(s,'Growing search column',[(x,y,.05),(x,y,1.5)],.006);p.parent=root
                    key(p.data,'bevel_factor_end',0,1);key(p.data,'bevel_factor_end',1,105)
        # Three thin computational layers expand under the suspended tiles.
        for z in [.2,.5,.8]:
            p=line(s,'Expanding calculation layer',[(-2.4,-2.4,z),(2.4,-2.4,z),(2.4,2.4,z),(-2.4,2.4,z),(-2.4,-2.4,z)],.009);p.parent=root
            key(p.data,'bevel_factor_end',0,1);key(p.data,'bevel_factor_end',1,100)
    s.render.image_settings.media_type='VIDEO';s.render.image_settings.file_format='FFMPEG'
    s.render.filepath=str(D/(kind+'.mp4'));s.frame_set(1)
    bpy.ops.file.pack_all();bpy.ops.wm.save_as_mainfile(filepath=str(D/(kind+'.blend')))
    s.render.image_settings.media_type='IMAGE';s.render.image_settings.file_format='PNG'
    s.render.resolution_percentage=50;s.frame_set(75);s.render.filepath=str(D/(kind+'-preview.png'))
    bpy.ops.render.render(write_still=True)
