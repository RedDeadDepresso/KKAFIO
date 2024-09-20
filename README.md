# KKAFIO: Koikatsu Auto File I/O
<p align="center">
  <img width="720" alt="Screenshot 2024-09-20 025946" src="https://github.com/user-attachments/assets/cd98f504-6886-4011-9ccf-ba440e7b5080">
</p>

## Features
KKAFIO is a Python-based script to automate file input and output operations for the game Koikatsu which include:
- Create Backup: It will create a .7z file which include the game Userdata, mods (Sideloader Modpack excluded), BepInEx folders. If a .7z already exists with the same name it will be overwritten.
- Filter & Convert KKS: Works the same as [FlYiNGPoTAToChiP's KK_SunshineCardFilter](https://github.com/FlYiNGPoTAToChiP/KK_SunshineCardFilter). Given a folder it find all KKS cards and move them into the folder _KKS_card_. If conversion is enabled it will also convert KKS cards and store them _KKS_to_KK_
- Install Chara: Given a folder with chara, coordinate, overlays and zipmod, it will automatically copy and paste them into the respective game folders. Also, if it finds any zip, rar, 7z it will automatically extract them. The script assume all chara are female so they will be installed in Userdata/female. 
- Remove Chara: The reverse process of install chara.  Given a folder with chara, coordinate, overlays and zipmod it will automatically look into game folders and delete them if their found. ONLY USE it if you have selected RENAME or REPLACE in file conflicts in install chara and the input folder DOES NOT contain any MALE chara.

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

Note: You may have to run as administrator if Koikatsu is saved in C:\Program Files (x86). To do it run cmd as administrator, type cd with the path of KAFIO folder and then type python KAFIO.py
Please feel free to use and modify KAFIO as you see fit. Your feedback and contributions are always welcome.

## Known Bugs
Here are some known issues with KKAFIO:

- When running Create Backup it won't display any output for a while. Don't worry, just wait.
- Any .png that cannot be classified as chara or coordinate will be treated as Overlays. It is not a big issue, you can go to Overlays folder, sort by date and delete any files that are not overlays.

## Acknowledgment
I'd like to express my gratitude to the following individuals, listed in no particular order:
- [zhiyiYo](https://github.com/zhiyiYo) for providing [PyQt-Fluent-Widgets](https://github.com/zhiyiYo/PyQt-Fluent-Widgets) allowing me to make the GUI.
- [FlYiNGPoTAToChiP](https://github.com/FlYiNGPoTAToChiP) For making KK_SunshineCardFilter open-source allowing me to implement it as a module. Also, they provided the method to distinguish chara and coordinate.
