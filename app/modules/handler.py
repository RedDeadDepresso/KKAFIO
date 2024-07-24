class Handler:
    def __str__(self) -> str:
        return "Handler"
    
    def loadConfig(self, config):
        self.gamePath = config.get("Core", "GamePath")
        self.config = config.get(str(self))
        
    def handle(self, request):
        pass

    def setNext(self, request):
        request.removeHandler()
        request.process()

