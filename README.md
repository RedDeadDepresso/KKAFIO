# KKAFIO: Koikatsu Auto File I/O
<p align="center">
<img width="720" alt="KKAFIO preview" src="https://github.com/user-attachments/assets/5acc50f1-baf3-476f-b625-fc0405bf4e2f">
</p>

## Features

**1. Create Backup**  
- Automatically creates a `.7z` file that includes:
  - `Userdata`
  - `Mods` (excluding Sideloader Modpack)
  - `BepInEx` folders
- If a `.7z` file with the same name already exists, it will be overwritten.

**2. Filter & Convert KKS**  
- Functions similarly to [FlYiNGPoTAToChiP's KK_SunshineCardFilter](https://github.com/FlYiNGPoTAToChiP/KK_SunshineCardFilter)
- Given a folder, the script:
  - Finds all KKS (Koikatsu Sunshine) cards and moves them into the folder `_KKS_card_`
  - **Optional:** If conversion is enabled, it also converts KKS cards and stores them in `_KKS_to_KK_`

**3. Install Chara**  
- Given a folder containing:
  - `Chara`, `Coordinate`, `Overlays`, and `Zipmod` files
- The script automatically copies and pastes the files into their respective game folders.
- Additionally, if it finds any compressed files (e.g., `.zip`, `.rar`, `.7z`), it will automatically extract them.

**4. Remove Chara**  
- Performs the reverse process of the "Install Chara" feature:
  - Given a folder with `Chara`, `Coordinate`, `Overlays`, and `Zipmod` files, it looks for and deletes these files from the game directories if found.
- **Important:** Only use this feature if you have selected **Rename** or **Replace** in file conflicts when using the "Install Chara" feature.

## Requirements 
- 7-Zip installed
- if running from source code, Python 3.11 or latest, installed and added to your system's PATH.

## Installation and Usage
Download the latest version and extract it, then run KKAFIO.exe.

If you want to run from source code, follow these steps to get KAFIO working:
1. Clone or download this repository.
2. Install the required packages using `pip3` with the command `pip3 install -r requirements.txt`.
4. Run `KKAFIO.py` and modify the settings to your preference.
5. Press Start

**Note**: You may have to run as administrator if Koikatsu is saved in C:\Program Files (x86). To do it run cmd as administrator, type cd with the path of KAFIO folder and then type python KAFIO.py
Please feel free to use and modify KAFIO as you see fit. Your feedback and contributions are always welcome.

## Known Bugs
Here are some known issues with KKAFIO:

- When running Create Backup it won't display any output for a while. Don't worry, just wait. You can check if 7-zip is running using Task Mangager.
- Any .png that cannot be classified as chara or coordinate will be treated as Overlays. It is not a big issue, you can go to Overlays folder, sort by date and delete any files that are not overlays.

## Acknowledgment
I'd like to express my gratitude to the following individuals, listed in no particular order:
- [zhiyiYo](https://github.com/zhiyiYo) for providing [PyQt-Fluent-Widgets](https://github.com/zhiyiYo/PyQt-Fluent-Widgets) allowing me to make the GUI.
- [Kiramei](https://github.com/Kiramei) for creating the logger. You can find the original [here](https://github.com/Kiramei/blue_archive_auto_script/blob/master/core/utils.py).
- [FlYiNGPoTAToChiP](https://github.com/FlYiNGPoTAToChiP) for making KK_SunshineCardFilter open-source allowing me to implement it as a module. Also, they provided the method to distinguish chara and coordinate.
- [Evaanxd](https://www.patreon.com/user?u=3125561) and [GaryuX](https://www.patreon.com/GaryuX) for [Ryuko Matoi card and image](https://www.pixiv.net/en/artworks/77738576).
