# KKAFIO: Koikatsu Auto File I/O
<p align="center">
<img width="720" alt="KKAFIO preview" src="https://github.com/user-attachments/assets/5acc50f1-baf3-476f-b625-fc0405bf4e2f">
</p>

## Features

KKAFIO is a Python-based script to automate file input and output operations for the game Koikatsu which include:
- **Create Backup**: It will create a .7z file which include the game Userdata, mods (Sideloader Modpack excluded), BepInEx folders. If a .7z already exists with the same name it will be overwritten.
- **Filter & Convert KKS**: Works the same as [FlYiNGPoTAToChiP's KK_SunshineCardFilter](https://github.com/FlYiNGPoTAToChiP/KK_SunshineCardFilter). Given a folder it find all KKS cards and move them into the folder _KKS_card_. If conversion is enabled it will also convert KKS cards and store them _KKS_to_KK_
- **Install Chara**: Given a folder with chara, coordinate, overlays and zipmod, it will automatically copy and paste them into the respective game folders. Also, if it finds any zip, rar, 7z it will automatically extract them.
- **Remove Chara**: The reverse process of install chara.  Given a folder with chara, coordinate, overlays and zipmod it will automatically look into game folders and delete them if they're found. ONLY USE it if you have selected RENAME or REPLACE in file conflicts in install chara.

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
