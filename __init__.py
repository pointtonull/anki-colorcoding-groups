# 2022-2024 Marc Schubert <schubert.mc.ai@gmail.com>

from anki import hooks
from aqt import mw
from aqt.qt import *
from aqt import gui_hooks
from aqt.utils import tooltip
import os
import json
import sys

from PyQt6.QtWidgets import (QApplication, QWidget, QPushButton, QMainWindow, QTextEdit,
                             QTableWidget, QTableWidgetItem, QVBoxLayout, QHBoxLayout,
                             QInputDialog, QColorDialog, QDialog, QLabel, QLineEdit, 
                             QMessageBox, QComboBox, QAbstractItemView)
from PyQt6.QtGui import QColor
from PyQt6.QtCore import Qt, pyqtSignal

addon_folder = os.path.dirname(__file__)

def _normalize_group_value(value):
    """Group values are {"color": str, "bold": bool}. Accepts a legacy bare
    colour string (from before per-group bold) and upgrades it."""
    if isinstance(value, dict):
        return {"color": value.get("color", ""), "bold": bool(value.get("bold", False))}
    return {"color": value or "", "bold": False}


def _normalize_config(data):
    """Return config in the normalized form:

        {"groups": {group_name: {"color": str, "bold": bool}, ...},
         "entries": [{"word": ..., "group": ...}, ...]}

    Transparently migrates older formats:
      * the flat list ([{"word", "group", "color"}, ...]) where colour was
        duplicated on every entry; and
      * groups whose value was a bare colour string (before per-group bold).
    """
    if isinstance(data, dict) and "entries" in data:
        groups = {str(k): _normalize_group_value(v)
                  for k, v in data.get("groups", {}).items()}
        entries = [
            {"word": e.get("word", ""), "group": e.get("group", "")}
            for e in data.get("entries", [])
        ]
        return {"groups": groups, "entries": entries}

    # Legacy format: a flat list with a colour on each entry.
    if isinstance(data, list):
        groups = {}
        entries = []
        for e in data:
            group = e.get("group", "")
            color = e.get("color", "")
            if group and color and group not in groups:
                groups[group] = {"color": color, "bold": False}
            entries.append({"word": e.get("word", ""), "group": group})
        return {"groups": groups, "entries": entries}

    return {"groups": {}, "entries": []}


def read_config():
    filepath = os.path.join(addon_folder, "user_files", "config.json")
    with open(filepath) as file:
        data = json.load(file)
    return _normalize_config(data)


def write_config(config):
    filepath = os.path.join(addon_folder, "user_files", "config.json")
    with open(filepath, "w") as file:
        file.write(json.dumps(_normalize_config(config)))
    return 0


def group_color(groups, name):
    """Colour string for a group ('' if unknown)."""
    g = groups.get(name)
    return g.get("color", "") if isinstance(g, dict) else (g or "")


def group_bold(groups, name):
    """Whether a group renders its words bold."""
    g = groups.get(name)
    return bool(g.get("bold", False)) if isinstance(g, dict) else False


def write_settings(settings):
    filepath = os.path.join(addon_folder,"user_files", "settings.json")
    with open(filepath, "w") as file:
        json.dump(settings, file)

def read_settings():
    filepath = os.path.join(addon_folder,"user_files", "settings.json")
    if os.path.exists(filepath):
        with open(filepath) as file:
            return json.load(file)
    return {}


def get_groups():
    """Return the {group_name: {"color", "bold"}} map."""
    return read_config()["groups"]


def assign_word_to_group(word, group, color=None, bold=None):
    """Add (or re-assign) a word to a group.

    A group that does not exist yet is created with the given colour/bold. For
    an existing group its style is only changed when color/bold are explicitly
    provided (so re-tagging a word doesn't clobber the group's look).

    Matching is case-insensitive: an existing entry for the same word is
    updated in place instead of creating a duplicate.
    """
    config = read_config()
    existing = config["groups"].get(group)
    if existing is None:
        config["groups"][group] = {"color": color or "#ffffff", "bold": bool(bold)}
    else:
        if color is not None:
            existing["color"] = color
        if bold is not None:
            existing["bold"] = bool(bold)
    word_lower = word.lower()
    found = False
    for entry in config["entries"]:
        if entry.get("word", "").lower() == word_lower:
            entry["group"] = group
            found = True
    if not found:
        config["entries"].append({"word": word, "group": group})
    write_config(config)

class ManualEditing(QMainWindow):
    data_saved = pyqtSignal()

    def __init__(self):
        super().__init__()

        self.setWindowTitle("Manual Editing of Data")
        self.layout = QVBoxLayout()
        self.text =QTextEdit()
        self.text.setText(json.dumps(read_config(), indent=2))

        self.saveButton = QPushButton("Save")
        self.saveButton.clicked.connect(self.save_btn_clicked)

        self.cancelButton = QPushButton("Cancel")
        self.cancelButton.clicked.connect(self.cancel_btn_clicked)

        self.btns = QWidget()
        self.btn_layout = QHBoxLayout()
        self.btn_layout.addWidget(self.cancelButton)
        self.btn_layout.addWidget(self.saveButton)
        self.btns.setLayout(self.btn_layout)

        self.layout.addWidget(self.text)
        self.layout.addWidget(self.btns)
        self.mayor_widget = QWidget()
        self.mayor_widget.setLayout(self.layout)

        self.setCentralWidget(self.mayor_widget)

    def save_btn_clicked(self):
        plain_text = self.text.toPlainText()
        try:
            write_config(json.loads(plain_text))
            self.data_saved.emit()
            self.close()
        except Exception as e:
            error_json = '{"groups": {"VEGF-Inhibitor": "MediumTurquoise", "TNF-Inhibitor": "DarkTurquoise"}, "entries": [{"word": "Ramucirumab", "group": "VEGF-Inhibitor"}, {"word": "Certolizumab", "group": "TNF-Inhibitor"}]}'
            QMessageBox.critical(self, "JSON Format Error", "Invalid JSON format. Please ensure the data is correctly formatted as a JSON object. Example format:\n" + error_json + "\nError details: " + str(e))
        

    def cancel_btn_clicked(self):
        self.close()


NEW_GROUP_ITEM = "➕ Create new group…"

# Sentinels used as the picker's result for its two action rows. The leading
# null byte keeps them from ever colliding with a real group name.
PICKER_NEW = "\x00__new_group__"
PICKER_MANAGE = "\x00__manage_groups__"


class EntryDialog(QDialog):
    """Add/edit a single word. Colour is not chosen here — it comes from the
    word's group. `groups` is a {name: color} dict; the dialog works on a copy
    and may create new groups, which the caller can read back via `.groups`.
    """

    def __init__(self, word='', group='', groups=None, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Edit Entry')
        self.groups = dict(groups) if groups else {}
        self._loading = False
        self._prev_index = 0

        layout = QVBoxLayout()

        # Word / phrase field
        self.word_edit = QLineEdit(word)
        layout.addWidget(QLabel('Word or phrase:'))
        layout.addWidget(self.word_edit)

        # Group selector
        layout.addWidget(QLabel('Group:'))
        self.group_combo = QComboBox()
        self._reload_groups(select=group)
        self.group_combo.currentIndexChanged.connect(self.on_group_changed)
        layout.addWidget(self.group_combo)

        # Read-only preview of the group's colour
        self.color_preview = QLabel()
        self.color_preview.setFixedHeight(22)
        layout.addWidget(self.color_preview)
        self._update_preview()

        # Buttons for save or cancel
        buttons_layout = QHBoxLayout()
        self.btn_save = QPushButton('Save')
        self.btn_save.clicked.connect(self.accept)
        self.btn_cancel = QPushButton('Cancel')
        self.btn_cancel.clicked.connect(self.reject)
        buttons_layout.addWidget(self.btn_save)
        buttons_layout.addWidget(self.btn_cancel)
        layout.addLayout(buttons_layout)

        self.setLayout(layout)

    def _reload_groups(self, select=''):
        self._loading = True
        self.group_combo.clear()
        self.group_combo.addItem('(no group)', '')
        for name in sorted(self.groups, key=str.lower):
            self.group_combo.addItem(_color_icon(group_color(self.groups, name)), name, name)
        self.group_combo.addItem(NEW_GROUP_ITEM, None)
        idx = self.group_combo.findData(select or '')
        self.group_combo.setCurrentIndex(idx if idx >= 0 else 0)
        self._prev_index = self.group_combo.currentIndex()
        self._loading = False

    def on_group_changed(self, index):
        if self._loading:
            return
        if self.group_combo.itemData(index) is None:  # the sentinel item
            self._create_group()
        else:
            self._prev_index = index
            self._update_preview()

    def _create_group(self):
        dialog = GroupPropertiesDialog(parent=self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            name, color, bold = dialog.get_data()
            if name:
                if name not in self.groups:
                    self.groups[name] = {"color": color, "bold": bold}
                self._reload_groups(select=name)
                self._update_preview()
                return
        # cancelled (or no name): revert the selection
        self._loading = True
        self.group_combo.setCurrentIndex(self._prev_index)
        self._loading = False

    def _update_preview(self):
        group = self.group_combo.currentData()
        color = group_color(self.groups, group) if group else ""
        if color:
            weight = "bold" if group_bold(self.groups, group) else "normal"
            self.color_preview.setStyleSheet(
                f"background-color: {color}; border: 1px solid #888; font-weight: {weight};")
            bold_txt = ", bold" if group_bold(self.groups, group) else ""
            self.color_preview.setText(f"  {group} — {color}{bold_txt}")
        else:
            self.color_preview.setStyleSheet("")
            self.color_preview.setText("  (ungrouped — no color)")

    def get_data(self):
        group = self.group_combo.currentData()
        return self.word_edit.text().strip(), (group or '')


class GroupPropertiesDialog(QDialog):
    """Create or edit a group: name, colour, and whether it renders bold.
    Returns (name, color, bold) via get_data()."""

    def __init__(self, name='', color='#ffffff', bold=False, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Group')
        self.color = QColor(color) if color else QColor('white')

        layout = QVBoxLayout()

        layout.addWidget(QLabel('Group name:'))
        self.name_edit = QLineEdit(name)
        layout.addWidget(self.name_edit)

        layout.addWidget(QLabel('Color:'))
        self.color_btn = QPushButton('Choose color')
        self.color_btn.clicked.connect(self.choose_color)
        layout.addWidget(self.color_btn)
        self._update_color_btn()

        self.bold_check = QCheckBox('Bold')
        self.bold_check.setChecked(bool(bold))
        layout.addWidget(self.bold_check)

        buttons = QHBoxLayout()
        self.btn_ok = QPushButton('OK')
        self.btn_ok.clicked.connect(self.accept)
        self.btn_cancel = QPushButton('Cancel')
        self.btn_cancel.clicked.connect(self.reject)
        buttons.addWidget(self.btn_ok)
        buttons.addWidget(self.btn_cancel)
        layout.addLayout(buttons)

        self.setLayout(layout)

    def choose_color(self):
        color = QColorDialog.getColor(self.color, self, 'Choose group color')
        if color.isValid():
            self.color = color
            self._update_color_btn()

    def _update_color_btn(self):
        self.color_btn.setStyleSheet(f'background-color: {self.color.name()}')

    def get_data(self):
        return self.name_edit.text().strip(), self.color.name(), self.bold_check.isChecked()


class GroupPickerDialog(QDialog):
    """Keyboard-first group chooser shown after the shortcut.

    Focus starts in the filter box: type to narrow the list, ↑/↓ (or PageUp/
    PageDown) to move, Enter to pick, Esc to cancel — no mouse needed. Two action
    rows are always offered at the bottom: create a new group (pre-named with the
    filter text) or open the group manager. Results are read back via
    get_result(): a group name, or one of PICKER_NEW / PICKER_MANAGE.
    """

    def __init__(self, word, groups, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Set color group")
        self.resize(320, 380)
        self.groups = dict(groups)
        self._result = None

        layout = QVBoxLayout()
        layout.addWidget(QLabel(f'Color “{word}” as:'))

        self.filter_edit = QLineEdit()
        self.filter_edit.setPlaceholderText("Type to filter or name a new group…")
        self.filter_edit.textChanged.connect(self._refresh)
        self.filter_edit.installEventFilter(self)
        layout.addWidget(self.filter_edit)

        self.list = QListWidget()
        self.list.itemActivated.connect(lambda _it: self._accept_current())
        layout.addWidget(self.list)

        self.setLayout(layout)
        self._refresh()
        self.filter_edit.setFocus()

    def filter_text(self):
        return self.filter_edit.text().strip()

    def _refresh(self):
        needle = self.filter_edit.text().strip().lower()
        self.list.clear()
        for name in sorted(self.groups, key=str.lower):
            if needle and needle not in name.lower():
                continue
            item = QListWidgetItem(_color_icon(group_color(self.groups, name)), name)
            item.setData(Qt.ItemDataRole.UserRole, name)
            self.list.addItem(item)

        typed = self.filter_text()
        new_label = f'➕ Create new group “{typed}”…' if typed else "➕ Create new group…"
        new_item = QListWidgetItem(new_label)
        new_item.setData(Qt.ItemDataRole.UserRole, PICKER_NEW)
        self.list.addItem(new_item)

        manage_item = QListWidgetItem("⚙ Manage groups…")
        manage_item.setData(Qt.ItemDataRole.UserRole, PICKER_MANAGE)
        self.list.addItem(manage_item)

        self.list.setCurrentRow(0)

    def _accept_current(self):
        item = self.list.currentItem()
        if item is None:
            return
        self._result = item.data(Qt.ItemDataRole.UserRole)
        self.accept()

    def get_result(self):
        return self._result

    def eventFilter(self, obj, event):
        # Keep typing focus in the filter box while ↑/↓ drive the list and Enter
        # accepts, so the whole dialog is operable without leaving the keyboard.
        if obj is self.filter_edit and event.type() == QEvent.Type.KeyPress:
            key = event.key()
            count = self.list.count()
            if count and key in (Qt.Key.Key_Down, Qt.Key.Key_Up,
                                 Qt.Key.Key_PageDown, Qt.Key.Key_PageUp):
                row = self.list.currentRow()
                if key == Qt.Key.Key_Down:
                    row = min(row + 1, count - 1)
                elif key == Qt.Key.Key_Up:
                    row = max(row - 1, 0)
                elif key == Qt.Key.Key_PageDown:
                    row = min(row + 5, count - 1)
                else:
                    row = max(row - 5, 0)
                self.list.setCurrentRow(row)
                return True
            if key in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
                self._accept_current()
                return True
        return super().eventFilter(obj, event)


class ShareDialog(QDialog):
    def __init__(self, data, parent=None):
        super().__init__(parent)
        self.data = data
        self.setWindowTitle("Export Your Dataset")
        self.setup_ui()

    def setup_ui(self):
        layout = QVBoxLayout()

        # Message Label
        message_label = QLabel("""
        <p><strong>Export your groups and words as a JSON file.</strong><br>
        Use it as a backup, copy it to another computer, or share it with others.</p>
        <p>To share it publicly, open an issue or pull request at
        <a href="https://github.com/pointtonull/anki-colorcoding-groups">github.com/pointtonull/anki-colorcoding-groups</a>.</p>
        <p>Please choose a location to save your dataset file below:</p>
        """)
        message_label.setOpenExternalLinks(True)
        message_label.setTextFormat(Qt.TextFormat.RichText)  


        layout.addWidget(message_label)

        # Save Button
        save_button = QPushButton("Save Dataset As...")
        save_button.clicked.connect(self.save_file)
        layout.addWidget(save_button)

        self.setLayout(layout)

    def save_file(self):
        options = QFileDialog.Option.DontUseNativeDialog
        file_name, _ = QFileDialog.getSaveFileName(self, 
                                                "Save File", 
                                                "", 
                                                "JSON Files (*.json)", 
                                                options=options)
        if file_name:
            with open(file_name, 'w') as f:
                json.dump(self.data, f, indent=4)
            self.accept()

class SettingsDialog(QDialog):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle('Settings')
        self.setFixedSize(360, 110)

        settings = self.read_settings()

        layout = QVBoxLayout()

        # Bold is configured per group now (see "Manage Groups…").
        self.autoColorCheckBox = QCheckBox("Automatically color all fields when a card opens")
        self.autoColorCheckBox.setChecked(settings.get('autoColorAllFields', False))
        layout.addWidget(self.autoColorCheckBox)

        self.btnSave = QPushButton("Save")
        self.btnSave.clicked.connect(self.save_settings)
        self.btnCancel = QPushButton("Cancel")
        self.btnCancel.clicked.connect(self.cancel_settings)
        layout.addWidget(self.btnSave)

        self.setLayout(layout)

    def cancel_settings(self):
        print("Changes canceled!")
        self.close()

    def save_settings(self):
        self.write_settings({
            'autoColorAllFields': self.autoColorCheckBox.isChecked(),
        })
        self.accept()

    @staticmethod
    def write_settings(settings):
        filepath = os.path.join(addon_folder,"user_files" ,"settings.json")  
        with open(filepath, "w") as file:
            json.dump(settings, file)

    @staticmethod
    def read_settings():
        filepath = os.path.join(addon_folder,"user_files" , "settings.json")
        if os.path.exists(filepath):
            with open(filepath) as file:
                return json.load(file)
        return {}



class GroupManagerDialog(QDialog):
    """Manage groups and their colours. Works on copies of `groups`/`entries`;
    the caller reads results back via `get_data()` after the dialog is accepted.

    Deleting a group leaves its words in place but ungrouped (uncolored until
    reassigned).
    """

    def __init__(self, groups, entries, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Manage Groups")
        self.resize(420, 400)
        self.groups = dict(groups)
        self.entries = [dict(e) for e in entries]

        layout = QVBoxLayout()

        self.btnAddGroup = QPushButton("Add Group")
        self.btnAddGroup.clicked.connect(self.add_group)
        layout.addWidget(self.btnAddGroup)

        self.table = QTableWidget()
        self.table.setColumnCount(5)
        self.table.setHorizontalHeaderLabels(["Group", "Color", "Bold", "Edit", "Delete"])
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        layout.addWidget(self.table)

        buttons = QHBoxLayout()
        self.btnSave = QPushButton("Save")
        self.btnSave.clicked.connect(self.accept)
        self.btnCancel = QPushButton("Cancel")
        self.btnCancel.clicked.connect(self.reject)
        buttons.addWidget(self.btnSave)
        buttons.addWidget(self.btnCancel)
        layout.addLayout(buttons)

        self.setLayout(layout)
        self.refresh()

    def refresh(self):
        self.table.clearContents()
        self.table.setRowCount(0)
        for name in sorted(self.groups, key=str.lower):
            color = group_color(self.groups, name)
            bold = group_bold(self.groups, name)
            row = self.table.rowCount()
            self.table.insertRow(row)
            self.table.setItem(row, 0, QTableWidgetItem(name))

            swatch = QTableWidgetItem(color)
            swatch.setBackground(QColor(color))
            self.table.setItem(row, 1, swatch)

            self.table.setItem(row, 2, QTableWidgetItem("Yes" if bold else "No"))

            btnEdit = QPushButton("Edit")
            btnEdit.clicked.connect(lambda _=False, n=name: self.edit_group(n))
            self.table.setCellWidget(row, 3, btnEdit)

            btnDelete = QPushButton("Delete")
            btnDelete.clicked.connect(lambda _=False, n=name: self.delete_group(n))
            self.table.setCellWidget(row, 4, btnDelete)

    def add_group(self):
        dialog = GroupPropertiesDialog(parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        name, color, bold = dialog.get_data()
        if not name:
            return
        if name in self.groups:
            QMessageBox.warning(self, "Group exists", f"A group named “{name}” already exists.")
            return
        self.groups[name] = {"color": color, "bold": bold}
        self.refresh()

    def edit_group(self, name):
        dialog = GroupPropertiesDialog(name, group_color(self.groups, name),
                                       group_bold(self.groups, name), parent=self)
        if dialog.exec() != QDialog.DialogCode.Accepted:
            return
        new_name, color, bold = dialog.get_data()
        if not new_name:
            return
        if new_name != name and new_name in self.groups:
            QMessageBox.warning(self, "Group exists", f"A group named “{new_name}” already exists.")
            return
        if new_name != name:  # rename: move the key and repoint its words
            self.groups.pop(name, None)
            for e in self.entries:
                if e.get("group") == name:
                    e["group"] = new_name
        self.groups[new_name] = {"color": color, "bold": bold}
        self.refresh()

    def delete_group(self, name):
        count = sum(1 for e in self.entries if e.get("group") == name)
        msg = f"Delete group “{name}”?"
        if count:
            msg += f"\n\n{count} word(s) will stay but become ungrouped (uncolored)."
        if QMessageBox.question(self, "Delete group", msg) != QMessageBox.StandardButton.Yes:
            return
        self.groups.pop(name, None)
        for e in self.entries:
            if e.get("group") == name:
                e["group"] = ""
        self.refresh()

    def get_data(self):
        return self.groups, self.entries


class ComplexWindow4(QMainWindow):
    def __init__(self):
        super().__init__()
        self.groups = {}
        self.entries = []
        self.initUI()

    def initUI(self):
        self.setWindowTitle("🎨 Color Coding 🌈 - Settings")
        self.setGeometry(100, 100, 600, 700)

        # Main layout
        main_layout = QVBoxLayout()

        # Top buttons: add a word, manage the groups (and their colours)
        top_layout = QHBoxLayout()
        self.btnAdd = QPushButton("Add Word")
        self.btnAdd.clicked.connect(self.add_word)
        self.btnGroups = QPushButton("Manage Groups…")
        self.btnGroups.clicked.connect(self.manage_groups)
        top_layout.addWidget(self.btnAdd)
        top_layout.addWidget(self.btnGroups)
        main_layout.addLayout(top_layout)

        # Table setup
        self.table = QTableWidget()
        header = self.table.horizontalHeader()
        header.setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.setColumnCount(5)
        self.table.setHorizontalHeaderLabels(["Word", "Group", "Color", "Edit", "Delete"])
        self.table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)  # Disable editing cells directly
        self.load_data()
        main_layout.addWidget(self.table)


        # Buttons at the bottom (changes save automatically, so there is no
        # explicit Save button — "Close" just dismisses the window).
        button_layout = QHBoxLayout()
        self.btnClose = QPushButton("Close")
        self.btnSettings = QPushButton("Font Settings")
        self.btnModifyManually = QPushButton("Modify Manually")
        self.btnShare = QPushButton("Share your Dataset")

        # Connect buttons to their functionalities
        self.btnClose.clicked.connect(self.close)
        self.btnModifyManually.clicked.connect(self.modify_manually)
        self.btnShare.clicked.connect(self.share_functionality)
        self.btnSettings.clicked.connect(self.open_settings_dialog)

        # Adding buttons to the horizontal layout
        button_layout.addWidget(self.btnClose)
        button_layout.addWidget(self.btnSettings)
        button_layout.addWidget(self.btnModifyManually)
        button_layout.addWidget(self.btnShare)

        main_layout.addLayout(button_layout)

        # Container widget
        container = QWidget()
        container.setLayout(main_layout)
        self.setCentralWidget(container)


    def persist(self):
        """Write the current model to disk. Called after every change so the
        window saves automatically."""
        try:
            write_config({"groups": self.groups, "entries": self.entries})
        except Exception as e:
            QMessageBox.critical(self, "Save Error", "Could not save the data.\nError details: " + str(e))

    def modify_manually(self):
        self.manual_editing = ManualEditing()
        self.manual_editing.data_saved.connect(self.load_data)
        self.manual_editing.show()
        print("Modify manually activated!")

    def load_data(self):
        config = read_config()
        self.groups = config["groups"]
        self.entries = config["entries"]
        self.refresh_table()

    def refresh_table(self):
        self.table.clearContents()
        self.table.setRowCount(0)
        for entry in self.entries:
            self.add_table_row(entry.get("word", ""), entry.get("group", ""))

    def add_table_row(self, word, group):
        row = self.table.rowCount()
        self.table.insertRow(row)
        self.table.setItem(row, 0, QTableWidgetItem(word))
        self.table.setItem(row, 1, QTableWidgetItem(group))

        # Colour is derived from the group and shown read-only.
        color = group_color(self.groups, group)
        label = color if color else "(ungrouped)"
        if color and group_bold(self.groups, group):
            label += " (bold)"
        color_item = QTableWidgetItem(label)
        if color:
            color_item.setBackground(QColor(color))
        self.table.setItem(row, 2, color_item)

        # Edit button
        btnEdit = QPushButton('Edit')
        btnEdit.clicked.connect(self.edit_word)
        self.table.setCellWidget(row, 3, btnEdit)

        # Delete button
        btnDelete = QPushButton('Delete')
        btnDelete.clicked.connect(self.delete_word)
        self.table.setCellWidget(row, 4, btnDelete)

    def add_word(self):
        dialog = EntryDialog(groups=self.groups, parent=self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            word, group = dialog.get_data()
            self.groups = dialog.groups  # may include a newly created group
            if word:
                self.entries.append({"word": word, "group": group})
            self.refresh_table()
            self.persist()

    def edit_word(self):
        button = self.sender()
        if not button:
            return
        row = self.table.indexAt(button.pos()).row()
        if not (0 <= row < len(self.entries)):
            return
        entry = self.entries[row]
        dialog = EntryDialog(entry.get("word", ""), entry.get("group", ""),
                             groups=self.groups, parent=self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            word, group = dialog.get_data()
            self.groups = dialog.groups
            if word:
                entry["word"] = word
                entry["group"] = group
            self.refresh_table()
            self.persist()

    def delete_word(self):
        button = self.sender()
        if not button:
            return
        row = self.table.indexAt(button.pos()).row()
        if 0 <= row < len(self.entries):
            del self.entries[row]
            self.refresh_table()
            self.persist()

    def manage_groups(self):
        dialog = GroupManagerDialog(self.groups, self.entries, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.groups, self.entries = dialog.get_data()
            self.refresh_table()
            self.persist()

    def share_functionality(self):
        data_to_share = read_config()
        share_dialog = ShareDialog(data_to_share)
        share_dialog.exec()


    def open_settings_dialog(self):
        self.settings_dialog = SettingsDialog(self)
        self.settings_dialog.show()
    

    
def complex_settings_gui():
    mw.myWidget = widget = ComplexWindow4()
    widget.show()


# Setup buttons
def setupButtons(buttons, editor):
    icon_path = os.path.join(addon_folder,"icon3232.png")
    btn = editor.addButton(icon_path,
                           "foo",
                           performConversion,
                           tip= "Color words in all fields",
                           label = "C",
                           # On macOS Qt maps "Ctrl" to ⌘; "Meta" maps to the
                           # physical Control key — so this is Control+Shift+H.
                           keys = "Meta+Shift+H",
                           rightside=True,
                           )
    buttons.append(btn)

    # Keyboard-driven "set color group" for the current selection. Text-only
    # ("G") button; its real point is the shortcut. As above, "Meta" is the
    # physical Control key on macOS, so this is Control+Shift+G.
    btn_group = editor.addButton(None,
                                 "colorcoding_setgroup",
                                 open_group_picker,
                                 tip="Set color group for selection",
                                 label="G",
                                 keys="Meta+Shift+G",
                                 rightside=True,
                                 )
    buttons.append(btn_group)


# Launch engine
def _resolve_colored_entries():
    """Flatten the config into [{word, group, color, bold}, ...] for the JS,
    resolving each entry's colour and bold from its group. Ungrouped entries (or
    entries whose group no longer exists) are skipped — they stay uncolored."""
    config = read_config()
    groups = config["groups"]
    data = []
    for entry in config["entries"]:
        group = entry.get("group", "")
        color = group_color(groups, group)
        if not color:
            continue
        data.append({"word": entry.get("word", ""),
                     "group": group,
                     "color": color,
                     "bold": group_bold(groups, group)})
    return data


def _run_colorizer(editor, init_call):
    webview = editor.web
    js_path = os.path.join(addon_folder, "changer_static.js")
    with open(js_path, "r", encoding="utf-8") as js_file:
        js = js_file.read()
    webview.eval(js)
    data = _resolve_colored_entries()
    webview.eval(f"{init_call}({json.dumps(data)})")


def performConversion(editor):
    # The "C" button / highlight shortcut (Control+Shift+G): colorize every field
    # in the editor, not just the focused one. Also used to re-show colours after
    # assigning a word to a group.
    _run_colorizer(editor, "initializeAllFields")


def colorize_all_fields(editor):
    # On note load: colorize every field, without moving the caret.
    # Off by default — enable in Font Settings ("Automatically color all fields").
    if not read_settings().get("autoColorAllFields", False):
        return
    _run_colorizer(editor, "initializeAllFields")


# JS that returns the currently selected text, reaching into the editor
# fields' shadow DOM (each field hosts an <anki-editable> in a shadow root).
_GET_SELECTION_JS = """
(function () {
    function pick(root) {
        try {
            if (root && root.getSelection) {
                var sel = root.getSelection();
                if (sel) {
                    var t = sel.toString().trim();
                    if (t) return t;
                }
            }
        } catch (e) {}
        return "";
    }
    var t = pick(document);
    if (t) return t;
    var els = document.querySelectorAll('*');
    for (var i = 0; i < els.length; i++) {
        if (els[i].shadowRoot) {
            t = pick(els[i].shadowRoot);
            if (t) return t;
        }
    }
    return "";
})()
"""


def _color_icon(color):
    """A small square swatch of the given colour for menu entries."""
    pixmap = QPixmap(16, 16)
    pixmap.fill(QColor(color))
    return QIcon(pixmap)


def assign_selection_to_group(editor, group, color=None, bold=None):
    """Grab the editor's selected text and store it under `group`, then recolor.

    For an existing group, leave color/bold as None so its style is kept."""
    def on_text(selected):
        word = (selected or "").strip()
        if not word:
            tooltip("Select a word in a field first.")
            return
        assign_word_to_group(word, group, color, bold)
        performConversion(editor)  # recolor the field so the change shows now
        tooltip(f"Added “{word}” to group “{group}”.")

    editor.web.evalWithCallback(_GET_SELECTION_JS, on_text)


def create_group_and_assign(editor):
    """Prompt for a new group (name + colour + bold), then assign the selection."""
    dialog = GroupPropertiesDialog(parent=editor.parentWindow)
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return
    name, color, bold = dialog.get_data()
    if not name:
        return
    assign_selection_to_group(editor, name, color, bold)


def _manage_groups_standalone(editor):
    """Open the group manager on the live config (outside the settings window)
    and persist the result. Returns True if changes were saved."""
    config = read_config()
    dialog = GroupManagerDialog(config["groups"], config["entries"],
                                editor.parentWindow)
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return False
    groups, entries = dialog.get_data()
    write_config({"groups": groups, "entries": entries})
    return True


def _pick_group_for_word(editor, word):
    """Show the keyboard-driven picker for `word` and act on the choice:
    assign to an existing group, create a new one, or open the group manager
    (then reopen the picker so the just-managed groups are pickable)."""
    groups = get_groups()
    dialog = GroupPickerDialog(word, groups, parent=editor.parentWindow)
    if dialog.exec() != QDialog.DialogCode.Accepted:
        return
    result = dialog.get_result()

    if result == PICKER_MANAGE:
        if _manage_groups_standalone(editor):
            performConversion(editor)  # colours may have changed
        _pick_group_for_word(editor, word)  # let them pick after managing
        return

    if result == PICKER_NEW:
        propdlg = GroupPropertiesDialog(name=dialog.filter_text(),
                                        parent=editor.parentWindow)
        if propdlg.exec() != QDialog.DialogCode.Accepted:
            return
        group, color, bold = propdlg.get_data()
        if not group:
            return
        assign_word_to_group(word, group, color, bold)
    else:
        group = result
        if not group:
            return
        assign_word_to_group(word, group)

    performConversion(editor)  # recolor the field so the change shows now
    tooltip(f"Added “{word}” to group “{group}”.")


def open_group_picker(editor):
    """Shortcut entry point: grab the selected text, then open the picker."""
    def on_text(selected):
        word = (selected or "").strip()
        if not word:
            tooltip("Select a word in a field first.")
            return
        _pick_group_for_word(editor, word)

    editor.web.evalWithCallback(_GET_SELECTION_JS, on_text)


def on_editor_context_menu(editor_webview, menu):
    editor = editor_webview.editor
    submenu = menu.addMenu("🎨 Set color group")

    groups = get_groups()
    for group in sorted(groups, key=str.lower):
        action = submenu.addAction(_color_icon(group_color(groups, group)), group)
        action.triggered.connect(
            lambda checked=False, g=group: assign_selection_to_group(editor, g)
        )

    if groups:
        submenu.addSeparator()
    new_action = submenu.addAction("Create new group…")
    new_action.triggered.connect(
        lambda checked=False: create_group_and_assign(editor)
    )


# Setup actions
action_settings = QAction("🎨 Color Coding 🌈 ", mw)
qconnect(action_settings.triggered, complex_settings_gui)
mw.form.menuTools.addAction(action_settings)


editors = []
def add_to_editors(editor)->None:
    editor = editor.web
    editors.append(editor)

gui_hooks.editor_did_init_buttons.append(setupButtons)
#gui_hooks.editor_did_init.append(setupLiveChanger)
gui_hooks.editor_did_init.append(add_to_editors)
gui_hooks.editor_will_show_context_menu.append(on_editor_context_menu)
gui_hooks.editor_did_load_note.append(colorize_all_fields)

