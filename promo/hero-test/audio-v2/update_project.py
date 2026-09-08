import bpy
from pathlib import Path
P=Path(__file__).resolve().parent
bpy.ops.wm.open_mainfile(filepath=str(P.parent/'full/Khatib-Premium-Launch.blend'))
s=bpy.data.scenes['EDIT / KHATIB / 32 seconds']
for strip in s.sequence_editor.strips:
    if strip.type=='SOUND':
        name='music.wav' if strip.name.startswith('Music /') else 'effects.wav' if strip.name.startswith('Effects /') else 'soundtrack.wav'
        strip.sound=bpy.data.sounds.load(str(P/name),check_existing=False)
s.render.filepath=str(P/'Khatib-New-Sound.mp4')
bpy.ops.file.pack_all()
bpy.ops.wm.save_as_mainfile(filepath=str(P/'Khatib-New-Sound.blend'))
bpy.ops.file.make_paths_relative()
bpy.ops.wm.save_as_mainfile(filepath=str(P/'Khatib-New-Sound.blend'))
