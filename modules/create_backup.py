import os

class CreateBackup:
    def __init__(self, config, file_manager):
        """Initializes the Bounty module.

        Args:
            config (Config): BAAuto Config instance
        """
        self.config = config
        self.file_manager = file_manager
        self.game_path = self.config.game_path
        folders = ["mods", "UserData", "BepInEx"]
        self.folders = [self.game_path[f] for f in folders if self.config.create_backup[f]]
        self.filename = self.config.create_backup["Filename"]
        self.output_path = self.config.create_backup["OutputPath"]
    
    def logic_wrapper(self):
        output_path = os.path.join(self.output_path, self.filename)
        self.file_manager.create_archive(self.folders, output_path)





    