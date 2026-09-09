"""
content_resolver.py — Shared PNG content-type dispatch for InstallContents
and UninstallContents.

Both tasks scan a folder of PNG cards and, based on card type, route each
file to the same set of game folders (chara / coordinate / scene / overlay) —
the only real difference is what "route" means (copy in vs delete). This used
to be ~60 lines of near-identical match/case logic duplicated in both files;
now it lives once here and each task only supplies the bits that actually
differ (the file action, and the five do_* toggles).
"""

from pathlib import Path

from utils.classifier import CardType, get_card_type, is_coordinate, is_male
from utils.config import GameType
from utils.logger import logger


class ContentTypeResolver:
    """Mixin providing `resolve_png`. Subclasses must set:

    - self.game_path, self.game_type, self.is_sunshine  (from Config)
    - self.do_chara, self.do_mods, self.do_coords, self.do_scenes, self.do_overlays  (bool)
    - self._file_action(label: str, image_path: Path, dest_folder) — copy_and_paste
      for InstallContents, find_and_remove for UninstallContents. Both
      FileManager methods share this exact signature.
    - self._unsupported_chara_message(card_type: CardType, unsupported_game: str) -> str
      — the exact wording differs between "not supported by" (install) and
      "not in ... install" (uninstall), so subclasses provide it.
    """

    def _file_action(self, label: str, image_path: Path, dest_folder) -> None:
        raise NotImplementedError

    def _unsupported_chara_reason(self, unsupported_game: str) -> str:
        """Return the parenthetical reason suffix, e.g. 'not supported by X'
        (install) vs 'not in X install' (uninstall)."""
        raise NotImplementedError

    def resolve_png(self, image_path: Path) -> None:
        image_bytes = image_path.read_bytes()
        card_type   = get_card_type(image_bytes)

        # Koikatsu Sunshine can load KK, KKSP, and native KKS cards, so
        # nothing is "unsupported" there. Vanilla Koikatsu/Koikatsu Party can
        # only load KK/KKSP cards — KKS cards are unsupported outside Sunshine.
        if self.is_sunshine:
            native_chara_types = (CardType.KK, CardType.KKSP, CardType.KKS)
            unsupported_chara  = ()
            unsupported_game   = GameType.KOIKATSU_SUNSHINE.value
        else:
            native_chara_types = (CardType.KK, CardType.KKSP)
            unsupported_chara  = (CardType.KKS,)
            unsupported_game   = self.game_type

        if card_type in native_chara_types:
            if not self.do_chara:
                return
            if is_male(image_bytes):
                self._file_action("CHARA M", image_path, self.game_path["charaMale"])
            else:
                self._file_action("CHARA F", image_path, self.game_path["charaFemale"])

        elif card_type in unsupported_chara:
            reason = self._unsupported_chara_reason(unsupported_game)
            logger.skipped("CHARA",
                f"{image_path.name} is a {card_type.value} card ({reason})")

        elif card_type == CardType.SCENE:
            if not self.do_scenes:
                return
            if "scene" in self.game_path:
                self._file_action("SCENE", image_path, self.game_path["scene"])
            else:
                logger.skipped("SCENE", f"{image_path.name} — Studio not installed, skipping")

        else:  # CardType.UNKNOWN — either a coordinate or an overlay
            if is_coordinate(image_bytes):
                if not self.do_coords:
                    return
                self._file_action("COORD", image_path, self.game_path["coordinate"])
            else:
                if not self.do_overlays:
                    return
                self._file_action("OVERLAYS", image_path, self.game_path["Overlays"])
