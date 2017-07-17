import os
from pathlib import Path
import numpy as np

from scripts.utils.LoggerFactory import LoggerFactory


class ProcessDataSaver:
    def __init__(self, file_path: str, file_name: str, log_level="debug"):
        """Loob salvestaja. 'File_path' on koht kuhu fail salvestatake (tavaliselt
        FolderConstants.SAVE_FOLDER) ja 'file_name' on faili nimi (tavaliselt klassimuutja, et
        oleks lihtsam pärast salvestatut laadida).

        Loggeri jaoks, 'log_type'. Kui logida ei taha pole vaja siis muutuja None'iks.
        Tavaline väärtus sellel on 'debug'.

        Selleks, et salvestatut laadida kasuta np.load*i. Selle jaoks klassi ei ole."""
        self.file_path_with_name = Path(file_path, file_name)

        if log_level is not None or log_level != '':
            self.logger = self.make_logger(file_name)
            self.logger.debug("ProcessDataSaver created. File path " + file_path
                              + ", file_name " + file_name)

    def save_data(self, **data):
        np.savez(str(os.path.abspath(self.file_path_with_name)), **data)

        if self.logger is not None:
            self.logger.info(data)

    def make_logger(self, file_name):
        logger_name = "LoggerFactory." + file_name
        return LoggerFactory.create(logger_name)
