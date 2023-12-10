import os

class CreateBackup:
    def __init__(self, config, file_manager):
        """Initializes the Bounty module.

        Args:
            config (Config): BAAuto Config instance
        """
        self.config = config
        self.file_manager = file_manager
        self.backup_folders = self.config.create_backup["GameFolders"]
        self.filename = self.config.create_backup["Filename"]
        self.output_path = self.config.create_backup["OutputPath"]
        self.game_path = self.config.game_path
    
    def logic_wrapper(self):
        selected_folders = [self.game_path[folder] for folder in self.backup_folders if self.backup_folders[folder]]
        output_path = os.path.join(self.output_path, self.filename)
        self.file_manager.create_archive(selected_folders, output_path)





    