import bpy, math, random
from pathlib import Path
from mathutils import Vector
P=Path(__file__).resolve().parent
random.seed(21)
bpy.ops.wm.read_factory_settings(use_empty=True)
s=bpy.context.scene;s.name='KHATIB / cinematic direction test'
s.render.engine='BLENDER_EEVEE';s.eevee.taa_render_samples=64;s.eevee.use_raytracing=False
s.render.resolution_x=1920;s.render.resolution_y=1080;s.render.resolution_percentage=100;s.render.fps=24;s.frame_start=1;s.frame_end=120
s.render.use_motion_blur=True;s.render.motion_blur_shutter=.45
s.view_settings.view_transform='AgX';s.view_settings.look='AgX - Medium High Contrast'
s.world=bpy.data.worlds.new('Dark studio');s.world.color=(.004,.006,.006)
def mat(name,c,metal=0,rough=.3,trans=0,emission=0):
    m=bpy.data.materials.new(name);m.use_nodes=True;n=m.node_tree.nodes.get('Principled BSDF');n.inputs['Base Color'].default_value=(*c,1);n.inputs['Metallic'].default_value=metal;n.inputs['Roughness'].default_value=rough;n.inputs['Transmission Weight'].default_value=trans;n.inputs['Coat Weight'].default_value=.65
    if emission:n.inputs['Emission Color'].default_value=(*c,1);n.inputs['Emission Strength'].default_value=emission
    return m
obsidian=mat('Obsidian glass',(.015,.038,.034),.58,.24,0)
silver=mat('Brushed titanium',(.30,.39,.36),.88,.22)
white=mat('Porcelain signal',(.7,.88,.8),.22,.24,0,.3)
mint=mat('Mint light',(.18,1,.35),.2,.18,0,5)
blue=mat('Cool edge light',(.12,.55,.8),.3,.18,0,3)
ground=mat('Infinite satin',(.007,.009,.012),.15,.55)
ground.node_tree.nodes.get('Principled BSDF').inputs['Coat Weight'].default_value=0
ground.node_tree.nodes.get('Principled BSDF').inputs['Specular IOR Level'].default_value=0
def key(o,prop,v,f):setattr(o,prop,v);o.keyframe_insert(data_path=prop,frame=f)
def cube(name,loc,scale,material,bevel=.06):
    bpy.ops.mesh.primitive_cube_add(size=1,location=loc);o=bpy.context.object;o.name=name;o.dimensions=scale;bpy.ops.object.transform_apply(location=False,rotation=False,scale=True);o.data.materials.append(material)
    if bevel:b=o.modifiers.new('Precision bevel','BEVEL');b.width=bevel;b.segments=4;o.modifiers.new('Weighted corner normals','WEIGHTED_NORMAL')
    return o
def path(name,points,material,width=.009):
    c=bpy.data.curves.new(name,'CURVE');c.dimensions='3D';c.bevel_depth=width;c.bevel_resolution=3;sp=c.splines.new('POLY');sp.points.add(len(points)-1)
    for p,co in zip(sp.points,points):p.co=(*co,1)
    c.materials.append(material);o=bpy.data.objects.new(name,c);s.collection.objects.link(o);return o
root=bpy.data.objects.new('Floating computational board',None);s.collection.objects.link(root);root.location=(1.35,0,.55)
for row in range(8):
    for col in range(8):
        x=(col-3.5)*.63;y=(row-3.5)*.63
        tile=cube(f'Glass square {col},{row}',(x,y,0),(.60,.60,.18),obsidian if (row+col)%2 else silver,.047);tile.parent=root
        dz=random.uniform(.3,1.5);key(tile,'location',(x*1.22,y*1.22,dz),1);key(tile,'location',(x,y,0),53+int(random.random()*13));key(tile,'location',(x,y,.018*math.sin(row+col)),120)
        if (row+col)%4==0:
            edge=path('Etched tile light',[(x-.24,y-.24,.11),(x+.24,y-.24,.11),(x+.24,y+.24,.11)],mint,.005);edge.parent=root
            key(edge,'scale',(0,0,0),1);key(edge,'scale',(1,1,1),60)
            # A suspended signal node makes the depth between layers visible.
            bpy.ops.mesh.primitive_uv_sphere_add(segments=12,ring_count=8,radius=.025,location=(x,y,.4+random.random()*.5));node=bpy.context.object;node.name='Neural signal';node.parent=root;node.data.materials.append(mint)
key(root,'rotation_euler',(0,0,-.16),1);key(root,'rotation_euler',(0,0,.07),120)

# Chess symbols are holographic vector graphics, not physical chess pieces.
for name,co in [('white-n',(-.95,-.95)),('white-q',(.31,-1.57)),('black-k',(.95,1.57)),('black-r',(-1.57,1.57)),('white-p',(1.57,-.31)),('black-p',(-.31,.95))]:
    m=bpy.data.materials.new('Hologram '+name);m.use_nodes=True;ns=m.node_tree.nodes;ns.clear();im=ns.new('ShaderNodeTexImage');im.image=bpy.data.images.load(str(P.parent/'software/assets'/(name+'.png')))
    em=ns.new('ShaderNodeEmission');em.inputs[0].default_value=(.48,1,.65,1);em.inputs[1].default_value=1.5;tr=ns.new('ShaderNodeBsdfTransparent');mix=ns.new('ShaderNodeMixShader');out=ns.new('ShaderNodeOutputMaterial');m.node_tree.links.new(im.outputs['Alpha'],mix.inputs[0]);m.node_tree.links.new(tr.outputs[0],mix.inputs[1]);m.node_tree.links.new(em.outputs[0],mix.inputs[2]);m.node_tree.links.new(mix.outputs[0],out.inputs[0]);m.surface_render_method='DITHERED'
    bpy.ops.mesh.primitive_plane_add(size=.53,location=(*co,.28));o=bpy.context.object;o.name='Holographic '+name;o.parent=root;o.data.materials.append(m)
    key(o,'location',(*co,1.2),1);key(o,'location',(*co,.28),62)

for points in [[(-.95,-.95,.31),(-.95,.31,.36),(.31,.31,.31)],[(.31,-1.57,.28),(.31,1.57,.33),(.95,1.57,.28)]]:
    p=path('Calculated route',points,mint,.014);p.parent=root;key(p.data,'bevel_factor_end',0,1);key(p.data,'bevel_factor_end',1,72)

# Layered rails form an abstract computation volume behind the board.
for i in range(9):
    y=-2.5+i*.62
    rail=path('Signal rail '+str(i),[(-5,y,-.6),(-2.8,y,-.6),(-2.0,y+.4,-.6)],blue if i%3==0 else mint,.006)
    key(rail.data,'bevel_factor_end',0,1);key(rail.data,'bevel_factor_end',1,45+i*3)
cube('Reflection floor',(0,0,-.6),(200,200,.1),ground,0)
def area(name,loc,power,color,size,target=(0,0,0),shape='RECTANGLE'):
    d=bpy.data.lights.new(name,'AREA');d.energy=power*.35;d.color=color;d.shape=shape;d.size=size;d.size_y=.8;d.specular_factor=.35;o=bpy.data.objects.new(name,d);s.collection.objects.link(o);o.location=loc;o.rotation_euler=(Vector(target)-o.location).to_track_quat('-Z','Y').to_euler();return o
area('Softbox key',(-3,-4,7),1700,(.78,.91,1),6)
area('Mint rim',(3,4,4),2300,(.25,1,.43),5)
area('White strip',(0,1,7),2200,(1,1,1),3)
area('Blue rim',(-5,2,2),1000,(.18,.55,1),4)

camdata=bpy.data.cameras.new('Cinema camera');cam=bpy.data.objects.new('Cinema camera',camdata);s.collection.objects.link(cam);s.camera=cam;camdata.lens=45;camdata.dof.use_dof=True;camdata.dof.aperture_fstop=2.8
focus=bpy.data.objects.new('Rack focus target',None);s.collection.objects.link(focus);camdata.dof.focus_object=focus
for f,loc,target,lens in [(1,(-1.3,-3.6,2.1),(0,-.8,.9),52),(36,(1.9,-4.7,3.8),(1.1,0,.6),48),(76,(6,-8,7),(0,0,.4),48),(120,(6.7,-8.7,7.3),(0,0,.4),48)]:
    key(cam,'location',loc,f);key(cam,'rotation_euler',(Vector(target)-Vector(loc)).to_track_quat('-Z','Y').to_euler(),f);key(camdata,'lens',lens,f);key(focus,'location',target,f)
key(camdata.dof,'aperture_fstop',2.8,1);key(camdata.dof,'aperture_fstop',5.6,76)

# Dimensional lettering moves into the camera's field, lit by the same studio.
font=bpy.data.fonts.load('/System/Library/Fonts/Supplemental/Arial Bold.ttf')
def label(body,size,x,y,z):
    c=bpy.data.curves.new(body,'FONT');c.body=body;c.font=font;c.size=size;c.extrude=.004;c.bevel_depth=.001;c.bevel_resolution=3;c.materials.append(white);o=bpy.data.objects.new(body,c);s.collection.objects.link(o);o.parent=cam;o.location=(x,y,z)
    key(o,'scale',(0,0,0),1);key(o,'scale',(0,0,0),67);key(o,'scale',(1,1,1),85);return o
label('KHATIB',.67,-3.0,.4,-10)
label('NEURAL CHESS ENGINE',.12,-2.98,.05,-10)

# Native compositor bloom creates optical halation on bright signals.
tree=bpy.data.node_groups.new('Cinematic halation','CompositorNodeTree');s.compositing_node_group=tree
tree.interface.new_socket(name='Image',in_out='OUTPUT',socket_type='NodeSocketColor')
r=tree.nodes.new('CompositorNodeRLayers');gl=tree.nodes.new('CompositorNodeGlare');gl.inputs['Type'].default_value='Fog Glow';gl.inputs['Quality'].default_value='High';gl.inputs['Threshold'].default_value=1.4;gl.inputs['Strength'].default_value=.08
out=tree.nodes.new('NodeGroupOutput');tree.links.new(r.outputs['Image'],gl.inputs['Image']);tree.links.new(gl.outputs['Image'],out.inputs['Image'])
s.render.use_compositing=True
s.render.image_settings.media_type='VIDEO';s.render.image_settings.file_format='FFMPEG';s.render.ffmpeg.format='MPEG4';s.render.ffmpeg.codec='H264';s.render.ffmpeg.constant_rate_factor='HIGH';s.render.ffmpeg.audio_codec='AAC';s.render.filepath=str(P/'hero-silent.mp4')
bpy.ops.file.pack_all();bpy.ops.wm.save_as_mainfile(filepath=str(P/'Khatib-3D-Test.blend'))
s.render.image_settings.media_type='IMAGE';s.render.image_settings.file_format='PNG';s.render.resolution_percentage=50
for f in [12,60,105]:s.frame_set(f);s.render.filepath=str(P/f'frame-{f}.png');bpy.ops.render.render(write_still=True)
