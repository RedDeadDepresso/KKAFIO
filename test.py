import concurrent.futures
import requests
import json
import patoolib
import logging
import shutil

from bs4 import BeautifulSoup
from pathlib import Path
from util.config import Config
from util.file_manager import FileManager
from modules.create_backup import CreateBackup
from modules.fc_kks import FilterConvertKKS
from modules.install_chara import InstallChara
from modules.remove_chara import RemoveChara

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

CWD = Path.cwd()
TEST_PATH = CWD / "test"
GAMEPATH = TEST_PATH / "Koikatsu Party"
BACKUP_PATH = TEST_PATH / "Backup"
DOWNLOAD_PATH = TEST_PATH / "Downloads"


if TEST_PATH.exists() and TEST_PATH.is_dir():
    shutil.rmtree(TEST_PATH)

paths_to_check = [
    TEST_PATH,
    BACKUP_PATH,
    DOWNLOAD_PATH,
    GAMEPATH / "UserData",
    GAMEPATH / "BepInEx",
    GAMEPATH / "mods",
    GAMEPATH / "UserData" / "chara" / "male",
    GAMEPATH / "UserData" / "chara" / "female",
    GAMEPATH / "UserData" / "coordinate",
    GAMEPATH / "UserData" / "Overlays"
]

for path in paths_to_check:
    path.mkdir(parents=True, exist_ok=True)

def create_config():
    """Creates and saves the configuration file."""
    data = {
        "Core": {"GamePath": str(GAMEPATH)},
        "CreateBackup": {
            "Enable": True,
            "OutputPath": str(BACKUP_PATH),
            "BepInEx": False,
            "Filename": "koikatsu_backup",
            "mods": False,
            "UserData": False,
        },
        "FilterConvertKKS": {
            "Convert": True,
            "InputPath": str(DOWNLOAD_PATH),
            "Enable": True,
        },
        "InstallChara": {
            "Password": "Skip",
            "FileConflicts": "Skip",
            "Enable": True,
            "InputPath": str(DOWNLOAD_PATH),
        },
        "RemoveChara": {
            "Enable": True,
            "InputPath": str(DOWNLOAD_PATH),
        },
    }

    config_path = TEST_PATH / 'config/config.json'
    config_path.parent.mkdir(parents=True, exist_ok=True)
    with open(config_path, "w") as f:
        json.dump(data, f, indent=4)
    return Config(config_path)


CONFIG = create_config()
FILE_MANAGER = FileManager(CONFIG)


def valid_link(link_button):
    """Check if a link is a valid download button."""
    return link_button.text == 'Download' and link_button.get('href')


def get_card_urls(bepis_url: str) -> list[str]:
    """Extract card download URLs from a bepis page."""
    try:
        response = requests.get(bepis_url)
        response.raise_for_status()
    except requests.RequestException as e:
        logger.error(f"Error fetching {bepis_url}: {e}")
        return []

    soup = BeautifulSoup(response.text, 'html.parser')
    link_buttons = soup.find_all('a', class_='btn btn-primary btn-sm')
    return ['https://db.bepis.moe' + link_button.get('href') for link_button in link_buttons if valid_link(link_button)]


def download_card(card_url: str) -> Path:
    """Download a card from a given URL."""
    card_name = Path(card_url).name
    card_path = DOWNLOAD_PATH / card_name

    try:
        with requests.get(card_url, stream=True) as response:
            response.raise_for_status()
            with open(card_path, "wb") as out_file:
                out_file.write(response.content)
        logger.info(f"Downloaded {card_name}")
        return card_path
    except requests.RequestException as e:
        logger.error(f"Error downloading {card_url}: {e}")
        return None


def download(bepis_url: str):
    """Download all cards from a given bepis URL using a thread pool."""
    download_urls = get_card_urls(bepis_url)
    with concurrent.futures.ThreadPoolExecutor() as executor:
        results = list(executor.map(download_card, download_urls))

    return set(filter(None, results))


def test_fckss(kk_cards, kks_cards):
    """Test FilterConvertKKS functionality."""
    fckss = FilterConvertKKS(CONFIG, FILE_MANAGER)
    fckss.run()
    
    filter_path = DOWNLOAD_PATH / '_KKS_card_'
    conversion_path = DOWNLOAD_PATH / '_KKS_to_KK_'

    assert filter_path.exists(), "Filter path does not exist."
    assert conversion_path.exists(), "Conversion path does not exist."

    for kk_path in kk_cards:
        card_name = kk_path.name
        filtered_kks_path = filter_path / card_name
        converted_kks_path = conversion_path / f"KKS2KK_{card_name}"

        assert not filtered_kks_path.exists(), f"{filtered_kks_path} exists but should not."
        assert not converted_kks_path.exists(), f"{converted_kks_path} exists but should not."
    
    for kks_path in kks_cards:
        card_name = kks_path.name
        filtered_kks_path = filter_path / card_name
        converted_kks_path = conversion_path / f"KKS2KK_{card_name}"
        assert filtered_kks_path.exists(), f"{filtered_kks_path} does not exist."
        assert converted_kks_path.exists(), f"{converted_kks_path} does not exist."
        kk_cards.add(converted_kks_path)


def test_install(kk_cards):
    """Test character installation."""
    install_chara = InstallChara(CONFIG, FILE_MANAGER)
    install_chara.run()

    chara_path = GAMEPATH / 'UserData' / 'chara' / 'female'

    for card in kk_cards:
        assert (chara_path / card.name).exists(), f"{card.name} not found in installed cards."


def test_backup(kk_cards):
    """Test backup functionality."""
    create_backup = CreateBackup(CONFIG, FILE_MANAGER)
    create_backup.run()

    koikatsu_backup = BACKUP_PATH / 'koikatsu_backup.7z'
    assert koikatsu_backup.exists(), "Backup file does not exist."

    outdir = BACKUP_PATH / 'outdir'
    patoolib.extract_archive(str(koikatsu_backup), outdir=str(outdir))

    for card in kk_cards:
        assert (outdir / "UserData" / "chara" / "female" / card.name).exists(), f"{card.name} missing in backup."


def test_remove():
    """Test character removal."""
    remove_chara = RemoveChara(CONFIG, FILE_MANAGER)
    remove_chara.run()

    chara_path = GAMEPATH / 'UserData' / 'chara' / 'female'
    assert not list(chara_path.iterdir()), "Character directory not empty after removal."


def main():
    """Main function to run all tests."""
    kk_cards = download('https://db.bepis.moe/koikatsu?type=steam&orderby=popularity')
    kks_cards = download('https://db.bepis.moe/koikatsu?type=sunshine&orderby=popularity')

    if kk_cards and kks_cards:
        test_fckss(kk_cards, kks_cards)
        test_install(kk_cards)
        test_backup(kk_cards)
        test_remove()
        shutil.rmtree(TEST_PATH)
    else:
        logger.error("No cards downloaded. Exiting.")


if __name__ == '__main__':
    main()
