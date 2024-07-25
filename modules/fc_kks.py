import os
import re as regex
import codecs
import shutil
from app.common.logger import logger

class FilterConvertKKS:
    def __init__(self, config, file_manager):
        """Initializes the Bounty module.

        Args:
            config (Config): BAAuto Config instance
        """
        self.config = config
        self.file_manager = file_manager
        self.convert = self.config.fc_kks["Convert"]

    def get_list(self, folder_path):
        new_list = []
        for root, dirs, files in os.walk(folder_path):
            for filename in files:
                if regex.match(r".*(\.png)$", filename):
                    new_list.append(os.path.join(root, filename))
            return new_list

    def check_png(self, card_path):
        with codecs.open(card_path, "rb") as card:
            data = card.read()
            card_type = 0
            if data.find(b"KoiKatuChara") != -1:
                card_type = 1
                if data.find(b"KoiKatuCharaSP") != -1:
                    card_type = 2
                elif data.find(b"KoiKatuCharaSun") != -1:
                    card_type = 3
            logger.info(f"[{card_type}]", f"{card_path}")
        return card_type

    def convert_kk(self, card_name, card_path, destination_path):
        with codecs.open(card_path, mode="rb") as card:
            data = card.read()

            replace_list = [
                [b"\x15\xe3\x80\x90KoiKatuCharaSun", b"\x12\xe3\x80\x90KoiKatuChara"],
                [b"Parameter\xa7version\xa50.0.6", b"Parameter\xa7version\xa50.0.5"],
                [b"version\xa50.0.6\xa3sex", b"version\xa50.0.5\xa3sex"],
            ]

            for text in replace_list:
                data = data.replace(text[0], text[1])

            new_file_path = os.path.normpath(os.path.join(destination_path, f"KKS2KK_{card_name}"))
            # print(f"new_file_path {new_file_path}")

            with codecs.open(new_file_path, "wb") as new_card:
                new_card.write(data)

    def logic_wrapper(self):
        path = self.config.fc_kks["InputPath"]
        kks_card_list = []
        kks_folder = "_KKS_card_"
        kks_folder2 = "_KKS_to_KK_"

        png_list = self.get_list(path)

        count = len(png_list)
        if count > 0:
            logger.info("SCRIPT", "0: unknown / 1: kk / 2: kksp / 3: kks")
            for png in png_list:
                if self.check_png(png) == 3:
                    kks_card_list.append(png)
        else:
            logger.success("SCRIPT", f"no PNG found")
            return

        count = len(kks_card_list)
        if count > 0:
            print(kks_card_list)

            target_folder = os.path.normpath(os.path.join(path, kks_folder))
            target_folder2 = os.path.normpath(os.path.join(path, kks_folder2))
            if not os.path.isdir(target_folder):
                os.mkdir(target_folder)

            if self.convert:
                logger.info("SCRIPT", f"Conversion to KK is [{self.convert}]")
                if not os.path.isdir(target_folder2):
                    os.mkdir(target_folder2)

            for card_path in kks_card_list:
                source = card_path
                card = os.path.basename(card_path)
                target = os.path.normpath(os.path.join(target_folder, card))

                # copy & convert before move
                if self.convert:
                    self.convert_kk(card, source, target_folder2)

                # move file
                shutil.move(source, target)

            if self.convert:
                logger.success("SCRIPT", f"[{count}] cards moved to [{kks_folder}] folder, converted and save to [{kks_folder2}] folder")
            else:
                logger.success("SCRIPT", f"[{count}] cards moved to [{kks_folder}] folder")
        else:
            logger.success("SCRIPT", f"no KKS card found")
