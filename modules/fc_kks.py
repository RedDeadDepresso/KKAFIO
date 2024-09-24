import shutil
from pathlib import Path
from util.logger import logger


class FilterConvertKKS:
    def __init__(self, config, file_manager):
        """Initializes the FilterConvertKKS module.

        Args:
            config (Config): KKAFIO Config instance
        """
        self.config = config
        self.file_manager = file_manager
        self.convert = self.config.fc_kks["Convert"]

    def get_list(self, folder_path):
        """Get list of PNG files in the folder."""
        folder = Path(folder_path)
        return [str(file) for file in folder.rglob("*.png")]

    def check_png(self, card_path):
        """Check the PNG file and return its type."""
        card_path = Path(card_path)
        with card_path.open("rb") as card:
            data = card.read()
            card_type = 0
            if b"KoiKatuChara" in data:
                card_type = 1
                if b"KoiKatuCharaSP" in data:
                    card_type = 2
                elif b"KoiKatuCharaSun" in data:
                    card_type = 3
            logger.info(f"[{card_type}]", f"{card_path}")
        return card_type

    def convert_kk(self, card_name, card_path, destination_path):
        """Convert KKS card to KK."""
        card_path = Path(card_path)  # Convert to Path object
        with card_path.open(mode="rb") as card:
            data = card.read()

            replace_list = [
                [b"\x15\xe3\x80\x90KoiKatuCharaSun", b"\x12\xe3\x80\x90KoiKatuChara"],
                [b"Parameter\xa7version\xa50.0.6", b"Parameter\xa7version\xa50.0.5"],
                [b"version\xa50.0.6\xa3sex", b"version\xa50.0.5\xa3sex"],
            ]

            for old_text, new_text in replace_list:
                data = data.replace(old_text, new_text)

            new_file_path = Path(destination_path) / f"KKS2KK_{card_name}"

            with new_file_path.open("wb") as new_card:
                new_card.write(data)

    def run(self):
        """Main logic for processing the KKS to KK conversion."""
        path = Path(self.config.fc_kks["InputPath"])
        kks_card_list = []
        kks_folder = path / "_KKS_card_"
        kks_folder2 = path / "_KKS_to_KK_"

        png_list = self.get_list(path)

        count = len(png_list)
        if count > 0:
            logger.info("SCRIPT", "0: unknown / 1: kk / 2: kksp / 3: kks")
            for png in png_list:
                if self.check_png(png) == 3:
                    kks_card_list.append(png)
        else:
            logger.success("SCRIPT", "No PNG files found")
            return

        count = len(kks_card_list)
        if count > 0:
            print(kks_card_list)

            # Create target directories if they don't exist
            kks_folder.mkdir(exist_ok=True)

            if self.convert:
                logger.info("SCRIPT", f"Conversion to KK is [{self.convert}]")
                kks_folder2.mkdir(exist_ok=True)

            for card_path in kks_card_list:
                source = Path(card_path)
                target = kks_folder / source.name

                # Copy & convert before moving
                if self.convert:
                    self.convert_kk(source.name, source, kks_folder2)

                # Move file
                shutil.move(str(source), str(target))

            if self.convert:
                logger.success("SCRIPT", f"[{count}] cards moved to [{kks_folder}] folder, converted and saved to [{kks_folder2}] folder")
            else:
                logger.success("SCRIPT", f"[{count}] cards moved to [{kks_folder}] folder")
        else:
            logger.success("SCRIPT: No KKS cards found")
