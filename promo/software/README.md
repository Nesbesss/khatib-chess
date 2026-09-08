# Khatib — software launch film

40 seconds · 1920×1080 · 30 fps.

The interface is an original promotional concept designed for this film. It is not a recording of the shipped visualizer or a claim that this UI is currently available. No physical chess footage or image generation is used in this version.

## Files

- `Khatib-Software-Final.mp4` — final mastered export.
- `Khatib-Software-Editable.blend` — packed Blender project in the video-editing workspace. Select `EDIT / 40 second master` for the typography/audio timeline, or `KHATIB / Software launch` for the animated interface, pieces, paths, camera, and panels.
- `Khatib-Software.blend` — the equivalent render-source project before workspace preparation.
- `build.py` — native Blender construction and animation source.
- `assets/analysis.json` and `assets/khatib-analysis.sse` — actual engine response used for the illustrated principal variation and root search evaluation. The demo position is separate from the asserted competitive wins.
- `prepare_assets.py` — captures a real engine analysis and prepares vector piece textures.

The champion and bot victories are creator-provided claims; the video keeps opponents anonymous and invents no win count or Elo rating. “Still improving” refers to ongoing development rather than a measured Elo increase.

## Production

The UI geometry, chessboard squares, textured piece planes, search panels, connecting lines, camera motion, and title animation are native Blender assets. The Blender master also contains separate music and sound-design stems. FFmpeg performs final loudness mastering and MP4 delivery encoding. The exploratory visualizer recordings in `captures/` are research material and are not used in the film.

## Audio credits

“Minimal Emotion” by Alejandro Magaña (A. M.), Mixkit track 160. Edited to the film's timing.
Source: https://mixkit.co/free-stock-music/techno/

Mixkit effects: Cinematic whoosh fast transition (1492), Cinematic whoosh deep impact (1143), Cinematic trailer riser (790), Big cinematic impact (788), Cinematic whoosh stutter (787).
Source: https://mixkit.co/free-sound-effects/cinematic/

Audio assets are used under the respective Mixkit Free Licenses: https://mixkit.co/license/

## Piece artwork

Chess piece vectors © Colin M. L. Burnett, obtained through `python-chess`'s SVG module. The original SVG files and PNG rasterizations are supplied in `assets`. The vectors are offered under GFDL, BSD, and GPL licenses; the GPL v2-or-later option is used for these artwork assets. Their corresponding source SVGs are included, and GPL v2 text is in `assets/PIECES-GPL-2.0.txt`.

Source and licensing reference: https://python-chess.readthedocs.io/en/latest/svg.html

The piece-artwork license applies to those artwork assets; it does not claim ownership of Khatib or the third-party music. Fonts are the Mac's installed Helvetica Neue, Arial Bold, and Menlo.

## Rebuild

Run `build.py` with Blender 5.2, then render scene `EDIT / 40 second master`. Run `prepare_workspace.py` to create the editing-workspace copy. The packed project includes its images, fonts, and audio. The previous stock-footage edition remains in `../edit/`.
