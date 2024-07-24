import os
import re as regex
import codecs
import shutil

from app.common.logger import logger
from app.modules.handler import Handler


class FilterConvertKKS(Handler):
    def __str__(self) -> str:
        return "Filter Convert KKS"
    
    def loadConfig(self, config):
        super().loadConfig(config)
        self.convert = self.config["Convert"]

    def getList(self, folderPath):
        newList = []
        for root, dirs, files in os.walk(folderPath):
            for filename in files:
                if regex.match(r".*(\.png)$", filename):
                    newList.append(os.path.join(root, filename))
        return newList

    def checkPng(self, cardPath):
        with codecs.open(cardPath, "rb") as card:
            data = card.read()
            cardType = 0
            if data.find(b"KoiKatuChara") != -1:
                cardType = 1
                if data.find(b"KoiKatuCharaSP") != -1:
                    cardType = 2
                elif data.find(b"KoiKatuCharaSun") != -1:
                    cardType = 3
            logger.info(f"[{cardType}]", f"{cardPath}")
        return cardType

    def convertKk(self, cardName, cardPath, destinationPath):
        with codecs.open(cardPath, mode="rb") as card:
            data = card.read()

            replaceList = [
                [b"\x15\xe3\x80\x90KoiKatuCharaSun", b"\x12\xe3\x80\x90KoiKatuChara"],
                [b"Parameter\xa7version\xa50.0.6", b"Parameter\xa7version\xa50.0.5"],
                [b"version\xa50.0.6\xa3sex", b"version\xa50.0.5\xa3sex"],
            ]

            for text in replaceList:
                data = data.replace(text[0], text[1])

            newFilePath = os.path.normpath(os.path.join(destinationPath, f"KKS2KK_{cardName}"))

            with codecs.open(newFilePath, "wb") as newCard:
                newCard.write(data)

    def handle(self, request):
        path = self.config.fc_kks["InputPath"]
        kksCardList = []
        kksFolder = "_KKS_card_"
        kksFolder2 = "_KKS_to_KK_"

        pngList = self.getList(path)

        count = len(pngList)
        if count > 0:
            logger.info("SCRIPT", "0: unknown / 1: kk / 2: kksp / 3: kks")
            for png in pngList:
                if self.checkPng(png) == 3:
                    kksCardList.append(png)
        else:
            logger.success("SCRIPT", f"no PNG found")
            return

        count = len(kksCardList)
        if count > 0:
            print(kksCardList)

            targetFolder = os.path.normpath(os.path.join(path, kksFolder))
            targetFolder2 = os.path.normpath(os.path.join(path, kksFolder2))
            if not os.path.isdir(targetFolder):
                os.mkdir(targetFolder)

            if self.convert:
                logger.info("SCRIPT", f"Conversion to KK is [{self.convert}]")
                if not os.path.isdir(targetFolder2):
                    os.mkdir(targetFolder2)

            for cardPath in kksCardList:
                source = cardPath
                card = os.path.basename(cardPath)
                target = os.path.normpath(os.path.join(targetFolder, card))

                # copy & convert before move
                if self.convert:
                    self.convertKk(card, source, targetFolder2)

                # move file
                shutil.move(source, target)

            if self.convert:
                logger.success("SCRIPT", f"[{count}] cards moved to [{kksFolder}] folder, converted and save to [{kksFolder2}] folder")
            else:
                logger.success("SCRIPT", f"[{count}] cards moved to [{kksFolder}] folder")
        else:
            logger.success("SCRIPT", f"no KKS card found")

        self.setNext(request)