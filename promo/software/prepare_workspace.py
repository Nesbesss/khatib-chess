"""Save the completed design in Blender's proper video-editing workspace."""
import bpy
from pathlib import Path

P = Path(__file__).resolve().parent
bpy.ops.wm.read_factory_settings(app_template='Video_Editing')
startup_scenes = list(bpy.data.scenes)
with bpy.data.libraries.load(str(P/'Khatib-Software.blend'), link=False) as (source, target):
    target.scenes = list(source.scenes)
master = bpy.data.scenes['EDIT / 40 second master']
for window in bpy.context.window_manager.windows:
    window.scene = master
master.frame_set(436)
for scene in startup_scenes:
    bpy.data.scenes.remove(scene)
for screen in bpy.data.screens:
    for area in screen.areas:
        if area.type != 'SEQUENCE_EDITOR':
            continue
        space = area.spaces.active
        space.show_seconds = True
bpy.ops.file.pack_all()
bpy.ops.wm.save_as_mainfile(filepath=str(P/'Khatib-Software-Editable.blend'))
print('Saved video-editing workspace with',len(master.sequence_editor.strips),'timeline strips')
