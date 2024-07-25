class Handler:
    def __str__(self) -> str:
        return "Handler"
    
    def loadConfig(self, config):
        self.gamePath = config.get("Core", "GamePath")
        key = str(self).replace(" ", "")
        self.config = config.get(key)
        
    def handle(self, request):
        pass

    def setNext(self, request):
        request.removeHandler()
        request.process()

