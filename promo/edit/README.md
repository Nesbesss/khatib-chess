# Khatib — Your move

40-second, 1920×1080, 30 fps promo. Rebuilt in Blender's native Video Sequence Editor after the HTML concept was rejected.

## Deliverables
- `Khatib-Promo.blend`: editable Blender 5.2 project, 42 footage cuts, animated native text strips, exposure hits, timeline markers, independent recorded music and SFX stems.
- `Khatib-Promo-Final.mp4`: mastered H.264/AAC export.
- `sources.json`: individual footage sources and license designation.

Open the .blend with the sibling `shots` directory and `../assets` intact. The Python build and preparation scripts allow rebuilding. Generated visuals depict illustrative chess scenes, not actual match recordings. The anonymous under-18 champion and bot victories are creator-provided claims. No rating or win count is asserted.

## Audio
Music: “Minimal Emotion” by Alejandro Magaña (A. M.), Mixkit track 160. Edited and retimed to 120 BPM; silence at 10–13 seconds.
https://mixkit.co/free-stock-music/techno/

Recorded sound effects from Mixkit:
- Cinematic whoosh fast transition — 1492
- Cinematic whoosh deep impact — 1143
- Cinematic trailer riser — 790
- Big cinematic impact — 788
- Cinematic whoosh stutter — 787
https://mixkit.co/free-sound-effects/cinematic/

Music and SFX are used under their respective Mixkit Free Licenses:
https://mixkit.co/license/
Keep the source recordings within this editing project rather than redistributing them as a standalone stock library. Consult the music license for uses beyond online/social video.

## Footage and fonts
Eight Mixkit chess clips, each with a source-page declaration permitting commercial or personal use under the Mixkit Stock Video Free License; source URLs in sources.json. The supplied example video was used for reference only and is not included in the final edit.

Fonts: locally installed Impact and Helvetica Neue. The editable project uses the local fonts; the MP4 contains rendered titles.

## Rebuild
From `/Users/nesbes/chess`:

```sh
python3 promo/edit/prepare.py
cd promo
python3 mix_audio.py
/Applications/Blender.app/Contents/MacOS/Blender -b --python edit/build_blender.py
/Applications/Blender.app/Contents/MacOS/Blender -b edit/Khatib-Promo.blend -a
```

The old HTML concept remains outside this `edit` directory and is not used in this version.
