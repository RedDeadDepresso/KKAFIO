import sys
import traceback
try:
    with open('traceback.log', 'w') as f:
        pass

    from util.config import Config
    from util.logger import logger    
    from util.file_manager import FileManager
    from modules.install_chara import InstallChara
    from modules.remove_chara import RemoveChara
    from modules.fc_kks import FilterConvertKKS
    from modules.create_backup import CreateBackup


    class Script:
        def __init__(self, config, file_manager):
            """Initializes the primary azurlane-auto instance with the passed in
            Config instance; 

            Args:
                config (Config): BAAuto Config instance
            """
            logger.logger_signal = None
            self.config = config
            self.file_manager = file_manager
            self.modules = {
                'InstallChara': None,
                'RemoveChara': None,
                'CreateBackup': None,
                'FilterConvertKKS': None,
            }
            if self.config.install_chara['Enable']:
                self.modules['InstallChara'] = InstallChara(self.config, self.file_manager)
            if self.config.remove_chara['Enable']:
                self.modules['RemoveChara'] = RemoveChara(self.config, self.file_manager)
            if self.config.create_backup['Enable']:
                self.modules['CreateBackup'] = CreateBackup(self.config, self.file_manager)
            if self.config.fc_kks["Enable"]:
                self.modules['FilterConvertKKS'] = FilterConvertKKS(self.config, self.file_manager)

        def run(self):
            for task in self.config.tasks:
                if self.modules[task]:
                    logger.info("SCRIPT", f'Start Task: {task}')
                    try:
                        self.modules[task].logic_wrapper()
                    except:
                        logger.error("SCRIPT", f'Task error: {task}. For more info, check the traceback.log file.')
                        with open('traceback.log', 'a') as f:
                            f.write(f'[{task}]\n')
                            traceback.print_exc(None, f, True)
                            f.write('\n')
                        sys.exit(1)
            sys.exit(0)
            
except:
    print(f'[ERROR] Script Initialisation Error. For more info, check the traceback.log file.')
    with open('traceback.log', 'w') as f:
        f.write(f'Script Initialisation Error\n')
        traceback.print_exc(None, f, True)
        f.write('\n')
        sys.exit(1)


if __name__ == "__main__":
    config = Config('app/config/config.json')
    file_manager = FileManager(config)
    script = Script(config, file_manager)
    script.run()
