"""
Contains the UI classes used to populate the various panes used by Mu.

Copyright (c) 2015-2017 Nicholas H.Tollervey and others (see the AUTHORS file).

This program is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

This program is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with this program.  If not, see <http://www.gnu.org/licenses/>.
"""
import sys
import os
import re
import platform
import logging
import signal
import string
import bisect
import os.path
import json
import configparser
from PyQt5.QtCore import (Qt, QProcess, QProcessEnvironment, pyqtSignal,
                          QTimer, QUrl)
from collections import deque
from PyQt5.QtWidgets import (QMessageBox, QTextEdit, QFrame, QListWidget,
                             QGridLayout, QLabel, QMenu, QApplication,
                             QTreeView, QInputDialog, QLineEdit)  # , QListWidgetItem)
from PyQt5.QtGui import (QKeySequence, QTextCursor, QCursor, QPainter,
                         QDesktopServices, QStandardItem)  # , QBrush, QColor)
from qtconsole.rich_jupyter_widget import RichJupyterWidget
from mu.interface.themes import Font
from mu.interface.themes import DEFAULT_FONT_SIZE
from mu.interface.dialogs import PutPyFileDialog


logger = logging.getLogger(__name__)


CHARTS = True
try:  # pragma: no cover
    from PyQt5.QtChart import QChart, QLineSeries, QChartView, QValueAxis
except ImportError:  # pragma: no cover
    logger.info('Unable to find QChart. Plotter button will not display.')
    QChartView = object
    CHARTS = False


class JupyterREPLPane(RichJupyterWidget):
    """
    REPL = Read, Evaluate, Print, Loop.

    Displays a Jupyter iPython session.
    """

    on_append_text = pyqtSignal(bytes)

    def __init__(self, theme='day', parent=None):
        super().__init__(parent)
        self.set_theme(theme)
        self.console_height = 10

    def _append_plain_text(self, text, *args, **kwargs):
        super()._append_plain_text(text, *args, **kwargs)
        self.on_append_text.emit(text.encode('utf-8'))

    def set_font_size(self, new_size=DEFAULT_FONT_SIZE):
        """
        Sets the font size for all the textual elements in this pane.
        """
        stylesheet = ("QWidget{font-size: " + str(new_size) +
                      "pt; font-family: Monospace;}")
        self.setStyleSheet(stylesheet)

    def zoomIn(self, delta=2):
        """
        Zoom in (increase) the size of the font by delta amount difference in
        point size upto 34 points.
        """
        old_size = self.font.pointSize()
        new_size = min(old_size + delta, 34)
        self.set_font_size(new_size)

    def zoomOut(self, delta=2):
        """
        Zoom out (decrease) the size of the font by delta amount difference in
        point size down to 4 points.
        """
        old_size = self.font.pointSize()
        new_size = max(old_size - delta, 4)
        self.set_font_size(new_size)

    def set_theme(self, theme):
        """
        Sets the theme / look for the REPL pane.
        """
        if theme == 'contrast':
            self.set_default_style(colors='nocolor')
        elif theme == 'night':
            self.set_default_style(colors='nocolor')
        else:
            self.set_default_style()

    def setFocus(self):
        """
        Override base setFocus so the focus happens to the embedded _control
        within this widget.
        """
        self._control.setFocus()


class MicroPythonREPLPane(QTextEdit):
    """
    REPL = Read, Evaluate, Print, Loop.

    This widget represents a REPL client connected to a BBC micro:bit running
    MicroPython.

    The device MUST be flashed with MicroPython for this to work.
    """

    def __init__(self, serial, theme='day', parent=None):
        super().__init__(parent)
        self.serial = serial
        self.setFont(Font().load())
        self.setAcceptRichText(False)
        self.setReadOnly(False)
        self.setUndoRedoEnabled(False)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self.context_menu)
        self.setObjectName('replpane')
        self.set_theme(theme)
        if self.serial:
            self.serial.setDataTerminalReady(False)
            # self.serial.setRequestToSend(False)
            # self.serial.write(b'\x04')

    def paste(self):
        """
        Grabs clipboard contents then sends down the serial port.
        """
        clipboard = QApplication.clipboard()
        if clipboard and clipboard.text():
            to_paste = json.dumps(clipboard.text())
            if to_paste.startswith('"') and to_paste.endswith('"'):
                to_paste = to_paste[1:-1]
            to_paste = to_paste.replace('\\r', '\r').replace('\\n', '\r').\
                replace('\r\r', '\r')
            self.serial.write(bytes(to_paste, 'utf8'))

    def context_menu(self):
        """
        Creates custom context menu with just copy and paste.
        """
        menu = QMenu(self)
        if platform.system() == 'Darwin':
            copy_keys = QKeySequence(Qt.CTRL + Qt.Key_C)
            paste_keys = QKeySequence(Qt.CTRL + Qt.Key_V)
        else:
            copy_keys = QKeySequence(Qt.CTRL + Qt.SHIFT + Qt.Key_C)
            paste_keys = QKeySequence(Qt.CTRL + Qt.SHIFT + Qt.Key_V)

        menu.addAction(_("Copy"), self.copy, copy_keys)
        menu.addAction(_("Paste"), self.paste, paste_keys)
        menu.exec_(QCursor.pos())

    def set_theme(self, theme):
        pass

    def keyPressEvent(self, data):
        """
        Called when the user types something in the REPL.

        Correctly encodes it and sends it to the connected device.
        """
        key = data.key()
        msg = bytes(data.text(), 'utf8')
        if key == Qt.Key_Backspace:
            msg = b'\b'
        elif key == Qt.Key_Delete:
            msg = b'\x1B[\x33\x7E'
        elif key == Qt.Key_Up:
            msg = b'\x1B[A'
        elif key == Qt.Key_Down:
            msg = b'\x1B[B'
        elif key == Qt.Key_Right:
            msg = b'\x1B[C'
        elif key == Qt.Key_Left:
            msg = b'\x1B[D'
        elif key == Qt.Key_Home:
            msg = b'\x1B[H'
        elif key == Qt.Key_End:
            msg = b'\x1B[F'
        elif (platform.system() == 'Darwin' and
                data.modifiers() == Qt.MetaModifier) or \
             (platform.system() != 'Darwin' and
                data.modifiers() == Qt.ControlModifier):
            # Handle the Control key. On OSX/macOS/Darwin (python calls this
            # platform Darwin), this is handled by Qt.MetaModifier. Other
            # platforms (Linux, Windows) call this Qt.ControlModifier. Go
            # figure. See http://doc.qt.io/qt-5/qt.html#KeyboardModifier-enum
            if Qt.Key_A <= key <= Qt.Key_Z:
                # The microbit treats an input of \x01 as Ctrl+A, etc.
                msg = bytes([1 + key - Qt.Key_A])
        elif (data.modifiers() == Qt.ControlModifier | Qt.ShiftModifier) or \
                (platform.system() == 'Darwin' and
                    data.modifiers() == Qt.ControlModifier):
            # Command-key on Mac, Ctrl-Shift on Win/Lin
            if key == Qt.Key_C:
                self.copy()
                msg = b''
            elif key == Qt.Key_V:
                self.paste()
                msg = b''
        self.serial.write(msg)

    def process_bytes(self, data):
        """
        Given some incoming bytes of data, work out how to handle / display
        them in the REPL widget.
        """
        tc = self.textCursor()
        # The text cursor must be on the last line of the document. If it isn't
        # then move it there.
        while tc.movePosition(QTextCursor.Down):
            pass
        i = 0
        while i < len(data):
            if data[i] == 8:  # \b
                tc.movePosition(QTextCursor.Left)
                self.setTextCursor(tc)
            elif data[i] == 13:  # \r
                pass
            elif len(data) > 1 and data[i] == 27 and data[i + 1] == 91:
                # VT100 cursor detected: <Esc>[
                i += 2  # move index to after the [
                regex = r'(?P<count>[\d]*)(;?[\d]*)*(?P<action>[ABCDKm])'
                try:
                    m = re.search(regex, data[i:].decode('utf-8'))
                except Exception as ex:
                    break
                if m:
                    # move to (almost) after control seq
                    # (will ++ at end of loop)
                    i += m.end() - 1

                    if m.group("count") == '':
                        count = 1
                    else:
                        count = int(m.group("count"))

                    if m.group("action") == "A":  # up
                        tc.movePosition(QTextCursor.Up, n=count)
                        self.setTextCursor(tc)
                    elif m.group("action") == "B":  # down
                        tc.movePosition(QTextCursor.Down, n=count)
                        self.setTextCursor(tc)
                    elif m.group("action") == "C":  # right
                        tc.movePosition(QTextCursor.Right, n=count)
                        self.setTextCursor(tc)
                    elif m.group("action") == "D":  # left
                        tc.movePosition(QTextCursor.Left, n=count)
                        self.setTextCursor(tc)
                    elif m.group("action") == "K":  # delete things
                        if m.group("count") == "":  # delete to end of line
                            tc.movePosition(QTextCursor.EndOfLine,
                                            mode=QTextCursor.KeepAnchor)
                            tc.removeSelectedText()
                            self.setTextCursor(tc)
            elif data[i] == 10:  # \n
                tc.movePosition(QTextCursor.End)
                self.setTextCursor(tc)
                self.insertPlainText(chr(data[i]))
            else:
                tc.deleteChar()
                self.setTextCursor(tc)
                self.insertPlainText(chr(data[i]))
            i += 1
        self.ensureCursorVisible()

    def clear(self):
        """
        Clears the text of the REPL.
        """
        self.setText('')


class MuFileList(QListWidget):
    """
    Contains shared methods for the two types of file listing used in Mu.
    """
    disable = pyqtSignal()
    list_files = pyqtSignal()
    set_message = pyqtSignal(str)

    def show_confirm_overwrite_dialog(self):
        """
        Display a dialog to check if an existing file should be overwritten.

        Returns a boolean indication of the user's decision.
        """
        msg = QMessageBox(self)
        msg.setIcon(QMessageBox.Information)
        msg.setText(_("File already exists; overwrite it?"))
        msg.setWindowTitle(_("File already exists"))
        msg.setStandardButtons(QMessageBox.Ok | QMessageBox.Cancel)
        return msg.exec_() == QMessageBox.Ok


class MicrobitFileList(MuFileList):
    """
    Represents a list of files on the micro:bit.
    """

    put = pyqtSignal(str)
    delete = pyqtSignal(str)

    def __init__(self, home):
        super().__init__()
        self.home = home
        self.setDragDropMode(QListWidget.DragDrop)

    def dropEvent(self, event):
        source = event.source()
        if isinstance(source, LocalFileList):
            file_exists = self.findItems(source.currentItem().text(),
                                         Qt.MatchExactly)
            if not file_exists or \
                    file_exists and self.show_confirm_overwrite_dialog():
                self.disable.emit()
                local_filename = os.path.join(self.home,
                                              source.currentItem().text())
                msg = _("Copying '{}' to micro:bit.").format(local_filename)
                logger.info(msg)
                self.set_message.emit(msg)
                self.put.emit(local_filename)

    def on_put(self, microbit_file):
        """
        Fired when the put event is completed for the given filename.
        """
        msg = _("'{}' successfully copied to micro:bit.").format(microbit_file)
        self.set_message.emit(msg)
        self.list_files.emit()

    def contextMenuEvent(self, event):
        menu = QMenu(self)
        delete_action = menu.addAction(_("Delete (cannot be undone)"))
        action = menu.exec_(self.mapToGlobal(event.pos()))
        if action == delete_action:
            self.disable.emit()
            microbit_filename = self.currentItem().text()
            logger.info("Deleting {}".format(microbit_filename))
            msg = _("Deleting '{}' from micro:bit.").format(microbit_filename)
            logger.info(msg)
            self.set_message.emit(msg)
            self.delete.emit(microbit_filename)

    def on_delete(self, microbit_file):
        """
        Fired when the delete event is completed for the given filename.
        """
        msg = _("'{}' successfully deleted from micro:bit.").\
            format(microbit_file)
        self.set_message.emit(msg)
        self.list_files.emit()


class EspFileList(MuFileList):
    """
    Represents a list of files on the mPython Board.
    """

    put = pyqtSignal(str)
    delete = pyqtSignal(str)
    run_py = pyqtSignal(str)
    run_content = pyqtSignal(str)
    load_py = pyqtSignal(str,str)
    stop_run_py = pyqtSignal()
    write_lib = pyqtSignal(str)
    set_default = pyqtSignal(str)
    rename = pyqtSignal(str,str)
    reset_firmware = pyqtSignal(str)

    def __init__(self, home):
        super().__init__()
        self.home = home
        self.setDragDropMode(QListWidget.DragDrop)

    def dropEvent(self, event):
        source = event.source()
        if isinstance(source, LocalFileList):
            file_exists = self.findItems(source.currentItem().text(),
                                         Qt.MatchExactly)
            if not file_exists or \
                    file_exists and self.show_confirm_overwrite_dialog():
                self.disable.emit()
                local_filename = os.path.join(self.home,
                                              source.currentItem().text())
                msg = _("Copying '{}' to mPython board.").format(local_filename)
                logger.info(msg)
                self.set_message.emit(msg)
                self.put.emit(local_filename)

    def on_put(self, esp_file):
        """
        Fired when the put event is completed for the given filename.
        """
        msg = _("'{}' successfully copied to mPython board, please wait for the list to refresh.").format(esp_file)
        self.set_message.emit(msg)
        self.list_files.emit()
        if esp_file.lower().endswith('.py'):
            config_dir = os.path.join(self.home, '__config__')
            ini_path = os.path.join(config_dir, 'mpython.ini')
            download_run = "1"
            if os.path.isfile(ini_path):
                cf = configparser.ConfigParser()
                cf.read(ini_path)
                if cf.has_section("common"):
                    try:
                        download_run = cf.get("common","downloadrun")
                    except Exception as ex:
                        print(ex)
            if download_run == "1":
                dialog = PutPyFileDialog(self)
                dialog.setup(esp_file, config_dir)
                if dialog.exec_():
                    self.run_py.emit(esp_file)

    def on_run_content(self, content):
        """
        Running in ESP32 memory.
        """
        msg = _("Running in real time ...")
        self.set_message.emit(msg)
        self.run_content.emit(content)

    def contextMenuEvent(self, event):
        menu = QMenu(self)        
        if self.currentItem() == None:
            write_lib_action = menu.addAction(_("Flash basic library (mpython.py)"))
            restore_action = menu.addAction(_("Recovery firmware (cannot be undone)"))
            action = menu.exec_(self.mapToGlobal(event.pos()))
            if action == write_lib_action:
                self.write_lib.emit(self.home)
            elif action == restore_action:
                mess = QMessageBox(self)
                mess.setIcon(QMessageBox.Information)
                mess.setText(_("Restoring to the original firmware will lose all "
                               "personal files. This operation is irreversible. "
                               "Press 'OK' to continue?"))
                mess.setWindowTitle(_("mPython2"))
                mess.setStandardButtons(QMessageBox.Ok | QMessageBox.Cancel)
                if mess.exec_() == QMessageBox.Ok:
                    self.reset_firmware.emit(self.home) 
            return
        load_action = menu.addAction(_("Open in Mu"))
        run_action = menu.addAction(_("Run selected file"))
        stop_action = menu.addAction(_("Stop running"))
        write_lib_action = menu.addAction(_("Flash basic library (mpython.py)"))
        setdft_action = menu.addAction(_("Run by default"))
        rename_action = menu.addAction(_("Rename"))
        restore_action = menu.addAction(_("Recovery firmware (cannot be undone)"))
        delete_action = menu.addAction(_("Delete (cannot be undone)"))
        action = menu.exec_(self.mapToGlobal(event.pos()))
        no_file_found = False
        if action == load_action:
            if self.currentItem() is not None:
                esp_filename = self.currentItem().text()
                self.load_py.emit(esp_filename, self.home)
            else:
                no_file_found = True
        elif action == delete_action:
            if self.currentItem() is not None:
                self.disable.emit()
                esp_filename = self.currentItem().text()
                logger.info("Deleting {}".format(esp_filename))
                msg = _("Deleting '{}' from mPython board.").format(esp_filename)
                logger.info(msg)
                self.set_message.emit(msg)
                self.delete.emit(esp_filename)
            else:
                no_file_found = True
        elif action == run_action:
            if self.currentItem() is not None:
                esp_filename = self.currentItem().text()
                if not esp_filename.lower().endswith('.py'):
                    msg = _('Only Python file can be run.')
                    self.set_message.emit(msg)
                else:
                    self.run_py.emit(esp_filename)
            else:
                no_file_found = True
        elif action == stop_action:
            self.stop_run_py.emit()
        elif action == write_lib_action:
            self.write_lib.emit(self.home)
        elif action == setdft_action:
            if self.currentItem() is not None:
                esp_filename = self.currentItem().text()
                if not esp_filename.lower().endswith('.py'):
                    msg = _('Only Python file can be set to run by default.')
                    self.set_message.emit(msg)
                elif "main.py" == esp_filename.lower():
                    msg = _("'{}' no need to operate.").format("main.py")
                    self.set_message.emit(msg)
                elif "boot.py" == esp_filename.lower():
                    msg = _("'{}' no need to operate.").format("boot.py")
                    self.set_message.emit(msg)
                elif "mpython.py" == esp_filename.lower():
                    msg = _("'{}' no need to operate.").format("mpython.py")
                    self.set_message.emit(msg)
                else:
                    self.set_default.emit(esp_filename)
            else:
                no_file_found = True
        elif action == rename_action:
            esp_filename = self.currentItem().text()
            name, okPressed = QInputDialog.getText(self, _('mPython2'),
                _('Rename to new name:'),
                QLineEdit.Normal, esp_filename)
            if okPressed and (len(name)!=0) and (esp_filename != name):
                self.rename.emit(esp_filename, name)
                #print(name)
        elif action == restore_action:
            mess = QMessageBox(self)
            mess.setIcon(QMessageBox.Information)
            mess.setText(_("Restoring to the original firmware will lose all "
                           "personal files. This operation is irreversible. "
                           "Press 'OK' to continue?"))
            mess.setWindowTitle(_("mPython2"))
            mess.setStandardButtons(QMessageBox.Ok | QMessageBox.Cancel)
            if mess.exec_() == QMessageBox.Ok:
                self.reset_firmware.emit(self.home)           
        if no_file_found is True:
            msg = _('No file selected.')
            self.set_message.emit(msg)

    def on_delete(self, esp_file):
        """
        Fired when the delete event is completed for the given filename.
        """
        msg = _("'{}' successfully deleted from mPython board, please wait for"
                " the list to refresh.").format(esp_file)
        self.set_message.emit(msg)
        self.list_files.emit()

    def on_load(self, esp_file):
        """
        Fired when the open event is completed for the given filename.
        """
        msg = _("'{}' successfully opened from mPython board.").format(esp_file)
        self.set_message.emit(msg)

    def on_run(self, esp_file):
        msg = _("Running '{}' from mPython board.").format(esp_file)
        self.set_message.emit(msg)
        
    def on_set_default(self, esp_file):
        msg = _("'{}' has been set as the default running program.").format(esp_file)
        self.set_message.emit(msg)

    def on_write_lib(self):
        msg = _("The basic library has been written to mPython board, please wait for the list to refresh.")
        self.set_message.emit(msg)
        self.list_files.emit()

    def on_rename(self, esp_filename, new_name):
        msg = _("The file '{}' has been renamed to '{}', please wait for the"
                " list to refresh.").format(esp_filename, new_name)
        self.set_message.emit(msg)
        self.list_files.emit()


class LocalFileList(MuFileList):
    """
    Represents a list of files in the Mu directory on the local machine.
    """

    get = pyqtSignal(str, str)
    open_file = pyqtSignal(str)
    delete = pyqtSignal(str)

    def __init__(self, home):
        super().__init__()
        self.home = home
        self.setDragDropMode(QListWidget.DragDrop)

    def dropEvent(self, event):
        source = event.source()
        if isinstance(source, MicrobitFileList):
            file_exists = self.findItems(source.currentItem().text(),
                                         Qt.MatchExactly)
            if not file_exists or \
                    file_exists and self.show_confirm_overwrite_dialog():
                self.disable.emit()
                microbit_filename = source.currentItem().text()
                local_filename = os.path.join(self.home,
                                              microbit_filename)
                msg = _("Getting '{}' from micro:bit. "
                        "Copying to '{}'.").format(microbit_filename,
                                                   local_filename)
                logger.info(msg)
                self.set_message.emit(msg)
                self.get.emit(microbit_filename, local_filename)
        elif isinstance(source, EspFileList):
            file_exists = self.findItems(source.currentItem().text(),
                                         Qt.MatchExactly)
            if not file_exists or \
                    file_exists and self.show_confirm_overwrite_dialog():
                self.disable.emit()
                microbit_filename = source.currentItem().text()
                local_filename = os.path.join(self.home,
                                              microbit_filename)
                msg = _("Getting '{}' from mPython board. "
                        "Copying to '{}'.").format(microbit_filename,
                                                   local_filename)
                logger.info(msg)
                self.set_message.emit(msg)
                self.get.emit(microbit_filename, local_filename)

    def on_get(self, microbit_file):
        """
        Fired when the get event is completed for the given filename.
        """
        msg = _("Successfully copied '{}' "
                "from the board to your computer.").format(microbit_file)
        self.set_message.emit(msg)
        self.list_files.emit()
        
    def on_load_py(self, local_file):
        filename = os.path.basename(local_file)
        msg = _("Successfully loaded '{}' from the board.").format(filename)
        self.set_message.emit(msg)
        self.open_file.emit(local_file)
        self.list_files.emit()
        
    def on_delete(self, local_file):
        msg = _("Successfully move file: '{}' to your trash.").format(local_file)
        self.set_message.emit(msg)
        self.list_files.emit()

    def contextMenuEvent(self, event):
        menu = QMenu(self)
        if self.currentItem() == None:
            refresh_action = menu.addAction(_("Refresh"))
            action = menu.exec_(self.mapToGlobal(event.pos()))
            if action == refresh_action:
                msg = _("Refresh local file list ...")
                self.set_message.emit(msg)
                self.list_files.emit()
            return
        local_filename = self.currentItem().text()
        # Get the file extension
        ext = os.path.splitext(local_filename)[1].lower()
        open_internal_action = None
        # Mu micro:bit mode only handles .py & .hex
        if ext == '.py' or ext == '.hex' or ext == '.txt' or ext == '.json' or ext == '.ini':
            open_internal_action = menu.addAction(_("Open in Mu"))
        # Open outside Mu (things get meta if Mu is the default application)
        refresh_action = menu.addAction(_("Refresh"))
        open_action = menu.addAction(_("Open"))
        delete_action = menu.addAction(_("Delete (move to trash)"))
        action = menu.exec_(self.mapToGlobal(event.pos()))
        if action == refresh_action:
            msg = _("Refresh local file list ...")
            self.set_message.emit(msg)
            self.list_files.emit()
        elif action == open_action:
            # Get the file's path
            path = os.path.join(self.home, local_filename)
            logger.info("Opening {}".format(path))
            msg = _("Opening '{}'").format(local_filename)
            logger.info(msg)
            self.set_message.emit(msg)
            # Let Qt work out how to open it
            QDesktopServices.openUrl(QUrl.fromLocalFile(path))
        elif action == open_internal_action:
            logger.info("Open {} internally".format(local_filename))
            # Get the file's path
            path = os.path.join(self.home, local_filename)
            # Send the signal bubbling up the tree
            self.open_file.emit(path)
        elif action == delete_action:
            path = os.path.join(self.home, local_filename)
            self.delete.emit(path)
            self.on_delete(path)


class FileSystemPane(QFrame):
    """
    Contains two QListWidgets representing the micro:bit and the user's code
    directory. Users transfer files by dragging and dropping. Highlighted files
    can be selected for deletion.
    """

    set_message = pyqtSignal(str)
    set_warning = pyqtSignal(str)
    list_files = pyqtSignal()
    open_file = pyqtSignal(str)

    def __init__(self, home):
        super().__init__()
        self.home = home
        self.font = Font().load()
        microbit_fs = MicrobitFileList(home)
        local_fs = LocalFileList(home)

        @local_fs.open_file.connect
        def on_open_file(file):
            # Bubble the signal up
            self.open_file.emit(file)

        layout = QGridLayout()
        self.setLayout(layout)
        microbit_label = QLabel()
        microbit_label.setText(_('Files on your micro:bit:'))
        local_label = QLabel()
        local_label.setText(_('Files on your computer:'))
        self.microbit_label = microbit_label
        self.local_label = local_label
        self.microbit_fs = microbit_fs
        self.local_fs = local_fs
        self.set_font_size()
        layout.addWidget(microbit_label, 0, 0)
        layout.addWidget(local_label, 0, 1)
        layout.addWidget(microbit_fs, 1, 0)
        layout.addWidget(local_fs, 1, 1)
        self.microbit_fs.disable.connect(self.disable)
        self.microbit_fs.set_message.connect(self.show_message)
        self.local_fs.disable.connect(self.disable)
        self.local_fs.set_message.connect(self.show_message)

    def disable(self):
        """
        Stops interaction with the list widgets.
        """
        self.microbit_fs.setDisabled(True)
        self.local_fs.setDisabled(True)
        self.microbit_fs.setAcceptDrops(False)
        self.local_fs.setAcceptDrops(False)

    def enable(self):
        """
        Allows interaction with the list widgets.
        """
        self.microbit_fs.setDisabled(False)
        self.local_fs.setDisabled(False)
        self.microbit_fs.setAcceptDrops(True)
        self.local_fs.setAcceptDrops(True)

    def show_message(self, message):
        """
        Emits the set_message signal.
        """
        self.set_message.emit(message)

    def show_warning(self, message):
        """
        Emits the set_warning signal.
        """
        self.set_warning.emit(message)

    def on_ls(self, microbit_files):
        """
        Displays a list of the files on the micro:bit.

        Since listing files is always the final event in any interaction
        between Mu and the micro:bit, this enables the controls again for
        further interactions to take place.
        """
        self.microbit_fs.clear()
        self.local_fs.clear()
        for f in microbit_files:
            self.microbit_fs.addItem(f)
        local_files = [f for f in os.listdir(self.home)
                       if os.path.isfile(os.path.join(self.home, f))]
        local_files.sort()
        for f in local_files:
            self.local_fs.addItem(f)
        self.enable()

    def on_ls_fail(self):
        """
        Fired when listing files fails.
        """
        self.show_warning(_("There was a problem getting the list of files on "
                            "the micro:bit. Please check Mu's logs for "
                            "technical information. Alternatively, try "
                            "unplugging/plugging-in your micro:bit and/or "
                            "restarting Mu."))
        self.disable()

    def on_put_fail(self, filename):
        """
        Fired when the referenced file cannot be copied onto the micro:bit.
        """
        self.show_warning(_("There was a problem copying the file '{}' onto "
                            "the micro:bit. Please check Mu's logs for "
                            "more information.").format(filename))

    def on_delete_fail(self, filename):
        """
        Fired when a deletion on the micro:bit for the given file failed.
        """
        self.show_warning(_("There was a problem deleting '{}' from the "
                            "micro:bit. Please check Mu's logs for "
                            "more information.").format(filename))

    def on_get_fail(self, filename):
        """
        Fired when getting the referenced file on the micro:bit failed.
        """
        self.show_warning(_("There was a problem getting '{}' from the "
                            "micro:bit. Please check Mu's logs for "
                            "more information.").format(filename))

    def set_theme(self, theme):
        pass

    def set_font_size(self, new_size=DEFAULT_FONT_SIZE):
        """
        Sets the font size for all the textual elements in this pane.
        """
        self.font.setPointSize(new_size)
        self.microbit_label.setFont(self.font)
        self.local_label.setFont(self.font)
        self.microbit_fs.setFont(self.font)
        self.local_fs.setFont(self.font)

    def zoomIn(self, delta=2):
        """
        Zoom in (increase) the size of the font by delta amount difference in
        point size upto 34 points.
        """
        old_size = self.font.pointSize()
        new_size = min(old_size + delta, 34)
        self.set_font_size(new_size)

    def zoomOut(self, delta=2):
        """
        Zoom out (decrease) the size of the font by delta amount difference in
        point size down to 4 points.
        """
        old_size = self.font.pointSize()
        new_size = max(old_size - delta, 4)
        self.set_font_size(new_size)

        
class EspFileSystemPane(QFrame):
    """
    Contains two QListWidgets representing the mPython and the user's code
    directory. Users transfer files by dragging and dropping. Highlighted files
    can be selected for deletion.
    """

    set_message = pyqtSignal(str, int)
    set_warning = pyqtSignal(str)
    list_files = pyqtSignal()
    open_file = pyqtSignal(str)

    def __init__(self, home):
        super().__init__()
        self.home = home
        self.font = Font().load()
        esp_fs = EspFileList(home)
        local_fs = LocalFileList(home)

        @local_fs.open_file.connect
        def on_open_file(file):
            # Bubble the signal up
            self.open_file.emit(file)

        layout = QGridLayout()
        self.setLayout(layout)
        esp_label = QLabel()
        esp_label.setText(_('Files on your mPython board:'))
        local_label = QLabel()
        local_label.setText(_('Files on your computer:'))
        self.esp_label = esp_label
        self.local_label = local_label
        self.esp_fs = esp_fs
        self.local_fs = local_fs
        self.set_font_size()
        layout.addWidget(esp_label, 0, 0)
        layout.addWidget(local_label, 0, 1)
        layout.addWidget(esp_fs, 1, 0)
        layout.addWidget(local_fs, 1, 1)
        self.esp_fs.disable.connect(self.disable)
        self.esp_fs.set_message.connect(self.show_message)
        self.local_fs.disable.connect(self.disable)
        self.local_fs.set_message.connect(self.show_message)

    def disable(self):
        """
        Stops interaction with the list widgets.
        """
        self.esp_fs.setDisabled(True)
        self.local_fs.setDisabled(True)
        self.esp_fs.setAcceptDrops(False)
        self.local_fs.setAcceptDrops(False)

    def enable(self):
        """
        Allows interaction with the list widgets.
        """
        self.esp_fs.setDisabled(False)
        self.local_fs.setDisabled(False)
        self.esp_fs.setAcceptDrops(True)
        self.local_fs.setAcceptDrops(True)

    def show_message(self, message, sec=2):
        """
        Emits the set_message signal.
        """
        self.set_message.emit(message, sec)

    def show_warning(self, message):
        """
        Emits the set_warning signal.
        """
        self.set_warning.emit(message)

    def on_ls(self, esp_files, dft_file):
        """
        Displays a list of the files on the micro:bit.

        Since listing files is always the final event in any interaction
        between Mu and the micro:bit, this enables the controls again for
        further interactions to take place.
        """
        self.esp_fs.clear()
        self.local_fs.clear()
        if "main.py" in esp_files:
            self.esp_fs.addItem("main.py")
        if "boot.py" in esp_files:
            self.esp_fs.addItem("boot.py")
        for f in esp_files:
            if "main.py" != f and "boot.py" != f and f.find(".") > 0:
                #if f == dft_file:
                #    new_item = QListWidgetItem(dft_file, self.esp_fs)
                #    new_item.setForeground(QBrush(QColor(51,102,153)))
                #else:
                self.esp_fs.addItem(f)
                
        local_files = [f for f in os.listdir(self.home)
                       if os.path.isfile(os.path.join(self.home, f))]
        local_files.sort()
        for f in local_files:
            self.local_fs.addItem(f)
        self.enable()

    def on_ls_fail(self):
        """
        Fired when listing files fails.
        """
        self.show_warning(_("There was a problem getting the list of files on "
                            "the mPython board. Please check Mu's logs for "
                            "technical information. Alternatively, try "
                            "unplugging/plugging-in your mPython board and/or "
                            "restarting Mu."))
        # self.disable()

    def on_put_fail(self, filename):
        """
        Fired when the referenced file cannot be copied onto the micro:bit.
        """
        self.show_warning(_("There was a problem copying the file '{}' onto "
                            "the mPython board. Please check Mu's logs for "
                            "more information.").format(filename))
        self.enable()

    def on_load_start(self, filename):
        self.show_message(_("Reading file '{}' from the mPython board ...").format(filename))
        
    def on_load_fail(self, error_txt):
        """
        Fired when the referenced file cannot be opened from the micro:bit.
        """
        self.show_message(_(error_txt))
                            
    def on_run_fail(self, error_txt):
        """
        Fired when the referenced file cannot be run on the micro:bit.
        """
        # self.show_message(_(error_txt))
        self.show_warning(_(error_txt))

    def on_info_start(self, info, sec):
        """
        Fired when the referenced file cannot be run on the micro:bit.
        """
        self.show_message(_(info), sec)
                            
    def on_set_default_fail(self, error_txt):
        self.show_message(_(error_txt))

    def on_write_lib_start(self):
        self.show_message(_("Flashing to board ..."))

    def on_write_lib_fail(self, error_txt):
        self.show_message(_(error_txt))

    def on_rename_start(self):
        self.show_message(_("Ready to rename ..."))

    def on_rename_fail(self, error_txt):
        self.show_message(_(error_txt))

    def on_delete_fail(self, filename):
        """
        Fired when a deletion on the micro:bit for the given file failed.
        """
        self.show_warning(_("There was a problem deleting '{}' from the "
                            "mPython board. Please check Mu's logs for "
                            "more information.").format(filename))

    def on_get_fail(self, filename):
        """
        Fired when getting the referenced file on the micro:bit failed.
        """
        self.show_warning(_("There was a problem getting '{}' from the "
                            "mPython board. Please check Mu's logs for "
                            "more information.").format(filename))

    def set_theme(self, theme):
        pass

    def set_font_size(self, new_size=DEFAULT_FONT_SIZE):
        """
        Sets the font size for all the textual elements in this pane.
        """
        self.font.setPointSize(new_size)
        self.esp_label.setFont(self.font)
        self.local_label.setFont(self.font)
        self.esp_fs.setFont(self.font)
        self.local_fs.setFont(self.font)

    def zoomIn(self, delta=2):
        """
        Zoom in (increase) the size of the font by delta amount difference in
        point size upto 34 points.
        """
        old_size = self.font.pointSize()
        new_size = min(old_size + delta, 34)
        self.set_font_size(new_size)

    def zoomOut(self, delta=2):
        """
        Zoom out (decrease) the size of the font by delta amount difference in
        point size down to 4 points.
        """
        old_size = self.font.pointSize()
        new_size = max(old_size - delta, 4)
        self.set_font_size(new_size)


class PythonProcessPane(QTextEdit):
    """
    Handles / displays a Python process's stdin/out with working command
    history and simple buffer editing.
    """

    on_append_text = pyqtSignal(bytes)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setFont(Font().load())
        self.setAcceptRichText(False)
        self.setReadOnly(False)
        self.setUndoRedoEnabled(False)
        self.setContextMenuPolicy(Qt.CustomContextMenu)
        self.customContextMenuRequested.connect(self.context_menu)
        self.running = False  # Flag to show the child process is running.
        self.setObjectName('PythonRunner')
        self.process = None  # Will eventually reference the running process.
        self.input_history = []  # history of inputs entered in this session.
        self.start_of_current_line = 0  # start position of the input line.
        self.history_position = 0  # current position when navigation history.

    def start_process(self, script_name, working_directory, interactive=True,
                      debugger=False, command_args=None, envars=None,
                      runner=None, python_args=None):
        """
        Start the child Python process.

        Will run the referenced Python script_name within the context of the
        working directory.

        If interactive is True (the default) the Python process will run in
        interactive mode (dropping the user into the REPL when the script
        completes).

        If debugger is True (the default is False) then the script will run
        within a debug runner session.

        If there is a list of command_args (the default is None), then these
        will be passed as further arguments into the script to be run.

        If there is a list of environment variables, these will be part of the
        context of the new child process.

        If runner is given, this is used as the command to start the Python
        process.

        If python_args is given, these are passed as arguments to the Python
        runtime used to launch the child process.
        """
        self.script = os.path.abspath(os.path.normcase(script_name))
        logger.info('Running script: {}'.format(self.script))
        if interactive:
            logger.info('Running with interactive mode.')
        if command_args is None:
            command_args = []
        logger.info('Command args: {}'.format(command_args))
        self.process = QProcess(self)
        self.process.setProcessChannelMode(QProcess.MergedChannels)
        # Force buffers to flush immediately.
        env = QProcessEnvironment.systemEnvironment()
        env.insert('PYTHONUNBUFFERED', '1')
        env.insert('PYTHONIOENCODING', 'utf-8')
        if sys.platform == 'darwin':
            parent_dir = os.path.dirname(__file__)
            if '.app/Contents/Resources/app/mu' in parent_dir:
                # Mu is running as a macOS app bundle. Ensure the expected
                # paths are in PYTHONPATH of the subprocess.
                env.insert('PYTHONPATH', ':'.join(sys.path))
        if envars:
            logger.info('Running with environment variables: '
                        '{}'.format(envars))
            for name, value in envars:
                env.insert(name, value)
        logger.info('Working directory: {}'.format(working_directory))
        self.process.setWorkingDirectory(working_directory)
        self.process.setProcessEnvironment(env)
        self.process.readyRead.connect(self.read_from_stdout)
        self.process.finished.connect(self.finished)
        logger.info('Python path: {}'.format(sys.path))
        if debugger:
            # Start the mu-debug runner for the script.
            parent_dir = os.path.join(os.path.dirname(__file__), '..')
            mu_dir = os.path.abspath(parent_dir)
            runner = os.path.join(mu_dir, 'mu-debug.py')
            python_exec = sys.executable
            args = [runner, self.script, ] + command_args
            self.process.start(python_exec, args)
        else:
            if runner:
                # Use the passed in Python "runner" to run the script.
                python_exec = runner
            else:
                # Use the current system Python to run the script.
                python_exec = sys.executable
            if interactive:
                # Start the script in interactive Python mode.
                args = ['-i', self.script, ] + command_args
            else:
                # Just run the command with no additional flags.
                args = [self.script, ] + command_args
            if python_args:
                args = python_args + args
            self.process.start(python_exec, args)
            self.running = True

    def finished(self, code, status):
        """
        Handle when the child process finishes.
        """
        self.running = False
        cursor = self.textCursor()
        cursor.movePosition(cursor.End)
        cursor.insertText('\n\n---------- FINISHED ----------\n')
        msg = 'exit code: {} status: {}'.format(code, status)
        cursor.insertText(msg)
        cursor.movePosition(QTextCursor.End)
        self.setTextCursor(cursor)
        self.setReadOnly(True)

    def context_menu(self):
        """
        Creates custom context menu with just copy and paste.
        """
        menu = QMenu(self)
        if platform.system() == 'Darwin':
            copy_keys = QKeySequence(Qt.CTRL + Qt.Key_C)
            paste_keys = QKeySequence(Qt.CTRL + Qt.Key_V)
        else:
            copy_keys = QKeySequence(Qt.CTRL + Qt.SHIFT + Qt.Key_C)
            paste_keys = QKeySequence(Qt.CTRL + Qt.SHIFT + Qt.Key_V)
        menu.addAction("Copy", self.copy, copy_keys)
        menu.addAction("Paste", self.paste, paste_keys)
        menu.exec_(QCursor.pos())

    def paste(self):
        """
        Grabs clipboard contents then writes to the REPL.
        """
        clipboard = QApplication.clipboard()
        if clipboard and clipboard.text():
            # normalize for Windows line-ends.
            text = '\n'.join(clipboard.text().splitlines())
            if text:
                self.parse_paste(text)

    def parse_paste(self, text):
        """
        Recursively takes characters from text to be parsed as input. We do
        this so the event loop has time to respond to output from the process
        to which the characters are sent (for example, when a newline is sent).

        Yes, this is a quick and dirty hack, but ensures the pasted input is
        also evaluated in an interactive manner rather than as a single-shot
        splurge of data. Essentially, it's simulating someone typing in the
        characters of the pasted text *really fast* but in such a way that the
        event loop cycles.
        """
        character = text[0]  # the current character to process.
        remainder = text[1:]  # remaining characters to process in the future.
        if character.isprintable() or character in string.printable:
            if character == '\n' or character == '\r':
                self.parse_input(Qt.Key_Enter, character, None)
            else:
                self.parse_input(None, character, None)
        if remainder:
            # Schedule a recursive call of parse_paste with the remaining text
            # to process. This allows the event loop to cycle and handle any
            # output from the child process as a result of the text pasted so
            # far (especially useful for handling responses from newlines).
            QTimer.singleShot(2, lambda text=remainder: self.parse_paste(text))

    def keyPressEvent(self, data):
        """
        Called when the user types something in the REPL.
        """
        key = data.key()
        text = data.text()
        modifiers = data.modifiers()
        self.parse_input(key, text, modifiers)

    def on_process_halt(self):
        """
        Called when the the user has manually halted a running process. Ensures
        that the remaining data from the halted process's stdout is handled
        properly.

        When the process is halted the user is dropped into the Python prompt
        and this method ensures the UI is updated in a clean, non-blocking
        way.
        """
        data = self.process.readAll().data()
        if data:
            self.append(data)
            self.on_append_text.emit(data)
            cursor = self.textCursor()
            self.start_of_current_line = cursor.position()

    def parse_input(self, key, text, modifiers):
        """
        Correctly encodes user input and sends it to the connected process.

        The key is a Qt.Key_Something value, text is the textual representation
        of the input, and modifiers are the control keys (shift, CTRL, META,
        etc) also used.
        """
        msg = b''  # Eventually to be inserted into the pane at the cursor.
        if key == Qt.Key_Enter or key == Qt.Key_Return:
            msg = b'\n'
        elif (platform.system() == 'Darwin' and
                modifiers == Qt.MetaModifier) or \
             (platform.system() != 'Darwin' and
                modifiers == Qt.ControlModifier):
            # Handle CTRL-C and CTRL-D
            if self.process and self.running:
                pid = self.process.processId()
                # NOTE: Windows related constraints don't allow us to send a
                # CTRL-C, rather, the process will just terminate.
                halt_flag = False
                if key == Qt.Key_C:
                    halt_flag = True
                    os.kill(pid, signal.SIGINT)
                if key == Qt.Key_D:
                    halt_flag = True
                    self.process.kill()
                if halt_flag:
                    # Clean up from kill signal.
                    self.process.readAll()  # Discard queued output.
                    # Schedule update of the UI after the process halts (in
                    # next iteration of the event loop).
                    QTimer.singleShot(1, self.on_process_halt)
                    return
        elif key == Qt.Key_Up:
            self.history_back()
        elif key == Qt.Key_Down:
            self.history_forward()
        elif key == Qt.Key_Right:
            cursor = self.textCursor()
            cursor.movePosition(QTextCursor.Right)
            self.setTextCursor(cursor)
        elif key == Qt.Key_Left:
            cursor = self.textCursor()
            if cursor.position() > self.start_of_current_line:
                cursor.movePosition(QTextCursor.Left)
                self.setTextCursor(cursor)
        elif key == Qt.Key_Home:
            cursor = self.textCursor()
            cursor.movePosition(QTextCursor.End)
            buffer_len = len(self.toPlainText()) - self.start_of_current_line
            for i in range(buffer_len):
                cursor.movePosition(QTextCursor.Left)
            self.setTextCursor(cursor)
        elif key == Qt.Key_End:
            cursor = self.textCursor()
            cursor.movePosition(QTextCursor.End)
            self.setTextCursor(cursor)
        elif (modifiers == Qt.ControlModifier | Qt.ShiftModifier) or \
                (platform.system() == 'Darwin' and
                    modifiers == Qt.ControlModifier):
            # Command-key on Mac, Ctrl-Shift on Win/Lin
            if key == Qt.Key_C:
                self.copy()
            elif key == Qt.Key_V:
                self.paste()
        elif text.isprintable():
            # If the key is for a printable character then add it to the
            # active buffer and display it.
            msg = bytes(text, 'utf8')
        if key == Qt.Key_Backspace:
            self.backspace()
        if key == Qt.Key_Delete:
            self.delete()
        if key == Qt.Key_Enter or key == Qt.Key_Return:
            # First move cursor to the end of the line and insert newline in
            # case return/enter is pressed while the cursor is in the
            # middle of the line
            cursor = self.textCursor()
            cursor.movePosition(QTextCursor.End)
            self.setTextCursor(cursor)
            self.insert(msg)
            # Then write line to std_in and add to history
            content = self.toPlainText()
            line = content[self.start_of_current_line:].encode('utf-8')
            self.write_to_stdin(line)
            if line.strip():
                self.input_history.append(line.replace(b'\n', b''))
            self.history_position = 0
            self.start_of_current_line = self.textCursor().position()
        elif not self.isReadOnly() and msg:
            self.insert(msg)

    def history_back(self):
        """
        Replace the current input line with the next item BACK from the
        current history position.
        """
        if self.input_history:
            self.history_position -= 1
            history_pos = len(self.input_history) + self.history_position
            if history_pos < 0:
                self.history_position += 1
                history_pos = 0
            history_item = self.input_history[history_pos]
            self.replace_input_line(history_item)

    def history_forward(self):
        """
        Replace the current input line with the next item FORWARD from the
        current history position.
        """
        if self.input_history:
            self.history_position += 1
            history_pos = len(self.input_history) + self.history_position
            if history_pos >= len(self.input_history):
                # At the most recent command.
                self.history_position = 0
                self.clear_input_line()
                return
            history_item = self.input_history[history_pos]
            self.replace_input_line(history_item)

    def read_from_stdout(self):
        """
        Process incoming data from the process's stdout.
        """
        data = self.process.read(256)
        if data:
            self.append(data)
            self.on_append_text.emit(data)
            cursor = self.textCursor()
            self.start_of_current_line = cursor.position()

    def write_to_stdin(self, data):
        """
        Writes data from the Qt application to the child process's stdin.
        """
        if self.process:
            self.process.write(data)

    def append(self, msg):
        """
        Append text to the text area.
        """
        cursor = self.textCursor()
        cursor.movePosition(QTextCursor.End)
        cursor.insertText(msg.decode('utf-8'))
        cursor.movePosition(QTextCursor.End)
        self.setTextCursor(cursor)

    def insert(self, msg):
        """
        Insert text to the text area at the current cursor position.
        """
        cursor = self.textCursor()
        if cursor.position() < self.start_of_current_line:
            cursor.movePosition(QTextCursor.End)
        cursor.insertText(msg.decode('utf-8'))
        self.setTextCursor(cursor)

    def backspace(self):
        """
        Removes a character from the current buffer -- to the left of cursor.
        """
        cursor = self.textCursor()
        if cursor.position() > self.start_of_current_line:
            cursor = self.textCursor()
            cursor.deletePreviousChar()
            self.setTextCursor(cursor)

    def delete(self):
        """
        Removes a character from the current buffer -- to the right of cursor.
        """
        cursor = self.textCursor()
        if cursor.position() >= self.start_of_current_line:
            cursor.deleteChar()
            self.setTextCursor(cursor)

    def clear_input_line(self):
        """
        Remove all the characters currently in the input buffer line.
        """
        cursor = self.textCursor()
        cursor.movePosition(QTextCursor.End)
        buffer_len = len(self.toPlainText()) - self.start_of_current_line
        for i in range(buffer_len):
            cursor.deletePreviousChar()
        self.setTextCursor(cursor)

    def replace_input_line(self, text):
        """
        Replace the current input line with the passed in text.
        """
        self.clear_input_line()
        self.append(text)

    def zoomIn(self, delta=2):
        """
        Zoom in (increase) the size of the font by delta amount difference in
        point size upto 34 points.
        """
        old_size = self.font().pointSize()
        new_size = old_size + delta
        if new_size <= 34:
            super().zoomIn(delta)

    def zoomOut(self, delta=2):
        """
        Zoom out (decrease) the size of the font by delta amount difference in
        point size down to 4 points.
        """
        old_size = self.font().pointSize()
        new_size = old_size - delta
        if new_size >= 4:
            super().zoomOut(delta)

    def set_theme(self, theme):
        pass


class DebugInspectorItem(QStandardItem):
    def __init__(self, *args):
        super().__init__(*args)
        self.setEditable(False)


class DebugInspector(QTreeView):
    """
    Presents a tree like representation of the current state of the call stack
    to the user.
    """

    def __init__(self):
        super().__init__()
        self.setUniformRowHeights(True)
        self.setSelectionBehavior(QTreeView.SelectRows)

    def set_font_size(self, new_size=DEFAULT_FONT_SIZE):
        """
        Sets the font size for all the textual elements in this pane.
        """
        stylesheet = ("QWidget{font-size: " + str(new_size) +
                      "pt; font-family: Monospace;}")
        self.setStyleSheet(stylesheet)

    def zoomIn(self, delta=2):
        """
        Zoom in (increase) the size of the font by delta amount difference in
        point size upto 34 points.
        """
        old_size = self.font().pointSize()
        new_size = min(old_size + delta, 34)
        self.set_font_size(new_size)

    def zoomOut(self, delta=2):
        """
        Zoom out (decrease) the size of the font by delta amount difference in
        point size down to 4 points.
        """
        old_size = self.font().pointSize()
        new_size = max(old_size - delta, 4)
        self.set_font_size(new_size)

    def set_theme(self, theme):
        pass


class PlotterPane(QChartView):
    """
    This plotter widget makes viewing sensor data easy!

    This widget represents a chart that will look for tuple data from
    the MicroPython REPL, Python 3 REPL or Python 3 code runner and will
    auto-generate a graph.
    """

    data_flood = pyqtSignal()

    def __init__(self, parent=None):
        super().__init__(parent)
        # Holds the raw input to be checked for actionable data to display.
        self.input_buffer = []
        # Holds the raw actionable data detected while plotting.
        self.raw_data = []
        self.setObjectName('plotterpane')
        self.max_x = 100  # Maximum value along x axis
        self.max_y = 1000  # Maximum value +/- along y axis
        self.flooded = False  # Flag to indicate if data flooding is happening.

        # Holds deques for each slot of incoming data (assumes 1 to start with)
        self.data = [deque([0] * self.max_x), ]
        # Holds line series for each slot of incoming data (assumes 1 to start
        # with).
        self.series = [QLineSeries(), ]

        # Ranges used for the Y axis (up to 1000, after which we just double
        # the range).
        self.y_ranges = [1, 5, 10, 25, 50, 100, 250, 500, 1000]

        # Set up the chart with sensible defaults.
        self.chart = QChart()
        self.chart.legend().hide()
        self.chart.addSeries(self.series[0])
        self.axis_x = QValueAxis()
        self.axis_y = QValueAxis()
        self.axis_x.setRange(0, self.max_x)
        self.axis_y.setRange(-self.max_y, self.max_y)
        self.axis_x.setLabelFormat("time")
        self.axis_y.setLabelFormat("%d")
        self.chart.setAxisX(self.axis_x, self.series[0])
        self.chart.setAxisY(self.axis_y, self.series[0])
        self.setChart(self.chart)
        self.setRenderHint(QPainter.Antialiasing)

    def process_bytes(self, data):
        """
        Takes raw bytes and, if a valid tuple is detected, adds the data to
        the plotter.

        The the length of the bytes data > 1024 then a data_flood signal is
        emitted to ensure Mu can take action to remain responsive.
        """
        # Data flooding guards.
        if self.flooded:
            return
        if len(data) > 1024:
            self.flooded = True
            self.data_flood.emit()
            return
        data = data.replace(b'\r\n', b'\n')
        self.input_buffer.append(data)
        # Check if the data contains a Python tuple, containing numbers, on a
        # single line (i.e. ends with \n).
        input_bytes = b''.join(self.input_buffer)
        lines = input_bytes.split(b'\n')
        for line in lines:
            if line.startswith(b'(') and line.endswith(b')'):
                # Candidate tuple. Extract the raw bytes into a numeric tuple.
                raw_values = [val.strip() for val in line[1:-1].split(b',')]
                numeric_values = []
                for raw in raw_values:
                    try:
                        numeric_values.append(int(raw))
                        # It worked, so move onto the next value.
                        continue
                    except ValueError:
                        # Try again as a float.
                        pass
                    try:
                        numeric_values.append(float(raw))
                    except ValueError:
                        # Not an int or float, so ignore this value.
                        continue
                if numeric_values:
                    # There were numeric values in the tuple, so use them!
                    self.add_data(tuple(numeric_values))
        # Reset the input buffer.
        self.input_buffer = []
        if lines[-1]:
            # Append any bytes that are not yet at the end of a line, for
            # processing next time we read data from self.serial.
            self.input_buffer.append(lines[-1])

    def add_data(self, values):
        """
        Given a tuple of values, ensures there are the required number of line
        series, add the data to the line series, update the range of the chart
        so the chart displays nicely.
        """
        # Store incoming data to dump as CSV at the end of the session.
        self.raw_data.append(values)
        # Check the number of incoming values.
        if len(values) != len(self.series):
            # Adjust the number of line series.
            value_len = len(values)
            series_len = len(self.series)
            if value_len > series_len:
                # Add new line series.
                for i in range(value_len - series_len):
                    new_series = QLineSeries()
                    self.chart.addSeries(new_series)
                    self.chart.setAxisX(self.axis_x, new_series)
                    self.chart.setAxisY(self.axis_y, new_series)
                    self.series.append(new_series)
                    self.data.append(deque([0] * self.max_x))
            else:
                # Remove old line series.
                for old_series in self.series[value_len:]:
                    self.chart.removeSeries(old_series)
                self.series = self.series[:value_len]
                self.data = self.data[:value_len]

        # Add the incoming values to the data to be displayed, and compute
        # max range.
        max_ranges = []
        for i, value in enumerate(values):
            self.data[i].appendleft(value)
            max_ranges.append(max([max(self.data[i]), abs(min(self.data[i]))]))
            if len(self.data[i]) > self.max_x:
                self.data[i].pop()

        # Re-scale y-axis.
        max_y_range = max(max_ranges)
        y_range = bisect.bisect_left(self.y_ranges, max_y_range)
        if y_range < len(self.y_ranges):
            self.max_y = self.y_ranges[y_range]
        elif max_y_range > self.max_y:
            self.max_y += self.max_y
        elif max_y_range < self.max_y / 2:
            self.max_y = self.max_y / 2
        self.axis_y.setRange(-self.max_y, self.max_y)

        # Ensure floats are used to label y axis if the range is small.
        if self.max_y <= 5:
            self.axis_y.setLabelFormat("%2.2f")
        else:
            self.axis_y.setLabelFormat("%d")

        # Update the line series with the data.
        for i, line_series in enumerate(self.series):
            line_series.clear()
            xy_vals = []
            for j in range(self.max_x):
                val = self.data[i][self.max_x - 1 - j]
                xy_vals.append((j, val))
            for point in xy_vals:
                line_series.append(*point)

    def set_theme(self, theme):
        """
        Sets the theme / look for the plotter pane.
        """
        if theme == 'day':
            self.chart.setTheme(QChart.ChartThemeLight)
        elif theme == 'night':
            self.chart.setTheme(QChart.ChartThemeDark)
        else:
            self.chart.setTheme(QChart.ChartThemeHighContrast)
