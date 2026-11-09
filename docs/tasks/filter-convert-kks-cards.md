# Filter & Convert KKS Cards

- Functions similarly to [FlYiNGPoTAToChiP's KK_SunshineCardFilter](https://github.com/FlYiNGPoTAToChiP/KK_SunshineCardFilter).
- Given a folder, the task scans recursively for PNG **character cards** and **Studio scenes**, then:
  - **Convert KKS → KK** _(off by default)_: produces a KK-compatible copy of each **KKS** (Koikatsu Sunshine) character card **and scene**, saved next to its original as `KKS2KK_<name>.png`. The copy is then treated as a KK file by **KK/KKSP Cards** below (not by **KKS Cards**).
    - **Character cards** get a header patch so KK accepts them.
    - **Scenes** are fully transcoded to the KK scene layout (ported from [KoikatsuSceneConverter](https://github.com/maguro-alternative/KoikatsuSceneConverter)): the scene version is rewritten to `1.0.4.2`; KKS-only fields (item `animePattern`, scene `shaderType` / `SkyInfo`) are removed; embedded KKS characters are down-converted; the background path is reduced to a file name; and KKSPE Timeline entries are renamed to KKPE so KK keeps them.
    - **Text objects are removed** from converted scenes because KK has no equivalent (a warning with the count is logged). Sky and shader settings can't carry over either. Keep your original KKS scenes — loading a converted scene back into KKS can lose data.
  - **KK/KKSP Cards** _(Keep by default)_: what to do with every **KK / KKSP** card found — including any KKS card or scene just converted above. **Keep** leaves them where they are; **Move** moves them into `_KK_card_/`; **Delete** sends them to the Recycle Bin.
  - **KKS Cards** _(Keep by default)_: the same three choices, applied to the original **KKS** cards and scenes (never to their converted copies — those are covered by **KK/KKSP Cards**).
  - Scenes are identified by their scene version (KKS = `1.1.0.0` or newer), not by card markers. Scenes already in KK format are left alone regardless of the actions above.
- **Optional:** **Extract Archives** _(on by default)_: extracts ZIP / RAR / 7z archives before filtering — if **Convert KKS → KK** is off and both actions are left at **Keep**, the task only extracts archives (it doesn't scan the PNGs at all), so it's also useful purely as an archive-extraction step.
- **Archive Password** _(`Skip` by default)_: `Skip` ignores password-protected archives; `Request Password` prompts you for one when an archive needs it.
- An archive whose extraction folder (named after the archive, next to it) already exists is not extracted again.
- Has a separate archive password setting from Install Contents.
