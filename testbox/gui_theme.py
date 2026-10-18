from __future__ import annotations

from string import Template

from PySide6 import QtCore, QtGui, QtWidgets


THEME_LABELS = {"light": "白色面板", "dark": "黑色面板", "slate": "蓝灰面板"}
THEMES = {
    "dark": {
        "window": "#000000", "sidebar": "#0A0A0C", "panel": "#0D0D10", "input": "#09090B",
        "raised": "#18181B", "hover": "#222226", "border": "#3F3F46", "text": "#F8FAFC",
        "muted": "#CBD5E1", "disabled": "#A1A1AA", "accent": "#34D399", "primary": "#10B981",
        "primary_hover": "#34D399", "primary_pressed": "#059669", "on_primary": "#000000",
        "selection": "#14532D", "on_selection": "#FFFFFF", "success": "#6EE7B7",
        "success_bg": "#10251F", "warning": "#FBBF24", "warning_bg": "#2A2110",
        "danger": "#FDA4AF", "danger_bg": "#2B1418", "info": "#93C5FD", "info_bg": "#17243B",
        "abandoned": "#FDBA74", "abandoned_bg": "#2A1B0F",
    },
    "light": {
        "window": "#F3F5F8", "sidebar": "#FFFFFF", "panel": "#FFFFFF", "input": "#FFFFFF",
        "raised": "#EEF2F6", "hover": "#E2E8F0", "border": "#CBD5E1", "text": "#172033",
        "muted": "#475569", "disabled": "#64748B", "accent": "#047857", "primary": "#047857",
        "primary_hover": "#065F46", "primary_pressed": "#064E3B", "on_primary": "#FFFFFF",
        "selection": "#D1FAE5", "on_selection": "#064E3B", "success": "#166534",
        "success_bg": "#F0FDF4", "warning": "#854D0E", "warning_bg": "#FEFCE8",
        "danger": "#9F1239", "danger_bg": "#FFF1F2", "info": "#1E40AF", "info_bg": "#EFF6FF",
        "abandoned": "#9A3412", "abandoned_bg": "#FFF7ED",
    },
    "slate": {
        "window": "#182334", "sidebar": "#152030", "panel": "#203047", "input": "#142032",
        "raised": "#2A3C55", "hover": "#344B68", "border": "#526783", "text": "#F1F5F9",
        "muted": "#CBD5E1", "disabled": "#A3B3C8", "accent": "#93C5FD", "primary": "#93C5FD",
        "primary_hover": "#BFDBFE", "primary_pressed": "#60A5FA", "on_primary": "#142032",
        "selection": "#344B68", "on_selection": "#FFFFFF", "success": "#86EFAC",
        "success_bg": "#19382F", "warning": "#FDE68A", "warning_bg": "#3C321E",
        "danger": "#FDA4AF", "danger_bg": "#3D2333", "info": "#BFDBFE", "info_bg": "#203C5D",
        "abandoned": "#FDBA74", "abandoned_bg": "#3C2B20",
    },
}

STYLE_TEMPLATE = Template("""
* {
    font-family: -apple-system, BlinkMacSystemFont, "SF Pro Display", "SF Pro Text", "Helvetica Neue", "PingFang SC", "Segoe UI", "Microsoft YaHei UI", "Microsoft YaHei", Arial, sans-serif;
    font-size: 13px;
    color: $text;
}
QWidget { color: $text; }
QMainWindow, QStackedWidget#mainStack, QScrollArea, QScrollArea > QWidget > QWidget {
    background-color: $window;
}
QDialog, QMessageBox, QInputDialog, QFileDialog { background-color: $window; color: $text; }
QLabel { background: transparent; }
QToolTip {
    background-color: $raised;
    color: $text;
    border: 1px solid $border;
    border-radius: 6px;
    padding: 6px 10px;
}
#navSidebar {
    background-color: $sidebar;
    border-right: 1px solid $border;
}
#nav {
    background: transparent;
    border: none;
    outline: none;
}
#nav::item {
    color: $muted;
    min-height: 38px;
    padding-left: 14px;
    padding-right: 14px;
    border-radius: 8px;
    margin-bottom: 4px;
    font-weight: 500;
}
#nav::item:hover {
    background-color: $hover;
    color: $text;
}
#nav::item:selected {
    background-color: $selection;
    color: $on_selection;
    border: 1px solid $border;
    font-weight: 600;
}
#pageTitle {
    font-size: 22px;
    font-weight: 700;
    letter-spacing: -0.3px;
    color: $text;
}
#brandTitle {
    color: $text;
    font-size: 17px;
    font-weight: 700;
    letter-spacing: -0.2px;
}
#mutedText, #fieldKeyLabel {
    color: $muted;
    font-size: 12px;
}
#requiredLabel, #fieldErrorLabel {
    color: $danger;
    font-size: 12px;
    font-weight: 600;
}
#fieldErrorLabel {
    margin-top: 2px;
}
#actionText, #runningTitle {
    color: $accent;
    font-weight: 600;
}
#runningTitle {
    font-size: 17px;
    font-weight: 700;
}
#catalogStateText {
    color: $muted;
    font-size: 15px;
    font-weight: 600;
}
#fieldHeader {
    color: $muted;
    font-weight: 600;
    font-size: 12px;
}
#panelTitle {
    font-weight: 700;
    font-size: 14px;
}
#pageNumber {
    font-weight: 600;
    color: $muted;
}
#footerText {
    color: $muted;
    font-size: 11px;
}
#separator {
    color: $border;
}
#tagLabel, #tagLabelMuted, #tagLabelDanger, #tagLabelInfo {
    border-radius: 12px;
    padding: 3px 10px;
    font-size: 11px;
    font-weight: 600;
}
#tagLabel {
    background-color: $success_bg;
    color: $success;
    border: 1px solid $success;
}
#tagLabelMuted {
    background-color: $raised;
    color: $muted;
    border: 1px solid $border;
}
#tagLabelDanger {
    background-color: $danger_bg;
    color: $danger;
    border: 1px solid $danger;
}
#tagLabelInfo {
    background-color: $info_bg;
    color: $info;
    border: 1px solid $info;
}
#toolCard {
    background-color: $panel;
    border: 1px solid $border;
    border-radius: 12px;
}
#toolCard:hover {
    border-color: $accent;
    background-color: $hover;
}
#toolCardTitle {
    font-size: 15px;
    font-weight: 700;
}
#toolCardDesc {
    font-size: 12px;
    color: $muted;
}
#cardPanel, QGroupBox {
    background-color: $panel;
    border: 1px solid $border;
    border-radius: 12px;
}
QGroupBox {
    margin-top: 14px;
    font-weight: 600;
    padding-top: 16px;
}
QGroupBox::title {
    subcontrol-origin: margin;
    subcontrol-position: top left;
    left: 14px;
    padding: 0 8px;
    background-color: $window;
    color: $muted;
    font-size: 12px;
}
#tipBox {
    background-color: $success_bg;
    border: 1px solid $success;
    border-radius: 10px;
}
#tipTitle {
    color: $success;
    font-weight: 700;
    font-size: 12px;
}
#tipContent {
    color: $success;
    font-size: 11px;
}
QPushButton, QToolButton {
    min-height: 32px;
    padding: 0 14px;
    border-radius: 8px;
    font-weight: 500;
    background-color: $raised;
    color: $text;
    border: 1px solid $border;
}
QPushButton:hover, QToolButton:hover {
    background-color: $hover;
    border-color: $accent;
}
QPushButton:pressed, QToolButton:pressed {
    background-color: $input;
}
#primaryButton {
    background-color: $primary;
    color: $on_primary;
    border: 1px solid $primary;
    font-weight: 700;
    border-radius: 8px;
}
#primaryButton:hover {
    background-color: $primary_hover;
    border-color: $primary_hover;
    color: $on_primary;
}
#primaryButton:pressed {
    background-color: $primary_pressed;
    border-color: $primary_pressed;
    color: $on_primary;
}
#smallButton {
    min-height: 26px;
    padding: 0 10px;
    font-size: 12px;
    border-radius: 6px;
}
QPushButton:disabled, QToolButton:disabled, #primaryButton:disabled {
    background-color: $raised;
    border-color: $border;
    color: $disabled;
}
QLineEdit, QComboBox, QSpinBox, QDoubleSpinBox, QDateEdit, QDateTimeEdit {
    min-height: 32px;
    border: 1px solid $border;
    border-radius: 8px;
    padding: 0 10px;
    background-color: $input;
    color: $text;
    selection-background-color: $selection;
    selection-color: $on_selection;
}
QLineEdit:focus, QComboBox:focus, QSpinBox:focus, QDoubleSpinBox:focus, QDateEdit:focus, QDateTimeEdit:focus,
QPlainTextEdit:focus, QTextEdit:focus {
    border: 1px solid $accent;
}
QLineEdit:disabled, QComboBox:disabled, QSpinBox:disabled, QDoubleSpinBox:disabled, QDateEdit:disabled,
QDateTimeEdit:disabled, QPlainTextEdit:disabled, QTextEdit:disabled {
    background-color: $raised;
    color: $disabled;
}
QLineEdit[invalid="true"] {
    border: 1.5px solid $danger;
    background-color: $danger_bg;
    color: $danger;
}
QComboBox QAbstractItemView {
    background-color: $input;
    border: 1px solid $border;
    border-radius: 8px;
    color: $text;
    selection-background-color: $selection;
    selection-color: $on_selection;
    padding: 4px;
}
QPlainTextEdit, QTextEdit {
    border: 1px solid $border;
    border-radius: 8px;
    padding: 8px;
    background-color: $input;
    color: $text;
    selection-background-color: $selection;
    selection-color: $on_selection;
}
#codeOutput, #logOutput {
    font-family: "SF Mono", "Cascadia Mono", Consolas, "PingFang SC", "Microsoft YaHei UI", monospace;
    font-size: 12px;
}
QCheckBox, QRadioButton {
    background: transparent;
    color: $text;
    spacing: 8px;
}
QCheckBox:disabled, QRadioButton:disabled, QLabel:disabled {
    color: $disabled;
}
QProgressBar {
    background-color: $raised;
    border: 1px solid $border;
    border-radius: 6px;
    text-align: center;
    color: $text;
}
QProgressBar::chunk {
    background-color: $primary;
    border-radius: 5px;
}
QTableWidget, QTableView, QListWidget, QListView, QTreeView, QTreeWidget {
    background-color: $input;
    alternate-background-color: $panel;
    border: 1px solid $border;
    border-radius: 10px;
    gridline-color: $border;
    color: $text;
    selection-background-color: $selection;
    selection-color: $on_selection;
}
QListWidget::item:selected {
    background-color: $selection;
    color: $on_selection;
}
QHeaderView {
    background-color: $raised;
    border: none;
}
QHeaderView::section {
    background-color: $raised;
    color: $muted;
    font-weight: 600;
    padding: 8px;
    border: none;
    border-bottom: 1px solid $border;
}
QTableCornerButton::section {
    background-color: $raised;
    border: none;
    border-bottom: 1px solid $border;
}
QTabWidget::pane {
    border: 1px solid $border;
    background-color: $panel;
    border-radius: 8px;
}
QTabBar::tab {
    background-color: $raised;
    color: $muted;
    padding: 8px 14px;
    border: 1px solid $border;
    border-radius: 6px;
    margin-right: 4px;
}
QTabBar::tab:selected {
    background-color: $panel;
    color: $accent;
    border-color: $accent;
}
QMenu, QMenuBar {
    background-color: $panel;
    color: $text;
    border-radius: 8px;
}
QMenu::item:selected, QMenuBar::item:selected {
    background-color: $selection;
    color: $on_selection;
}
QScrollBar:vertical {
    border: none;
    background: transparent;
    width: 8px;
    margin: 0;
}
QScrollBar:horizontal {
    border: none;
    background: transparent;
    height: 8px;
    margin: 0;
}
QScrollBar::handle:vertical {
    background: $border;
    min-height: 24px;
    border-radius: 4px;
}
QScrollBar::handle:horizontal {
    background: $border;
    min-width: 24px;
    border-radius: 4px;
}
QScrollBar::handle:hover {
    background: $muted;
}
QScrollBar::add-line, QScrollBar::sub-line {
    border: none;
    background: none;
}
#statusBannerSuccess, #statusBannerWarning, #statusBannerFailed, #statusBannerAbandoned, #statusBannerCancelled {
    border-radius: 12px;
}
#statusBannerSuccess {
    background-color: $success_bg;
    border: 1px solid $success;
}
#statusBannerWarning {
    background-color: $warning_bg;
    border: 1px solid $warning;
}
#statusBannerFailed, #diagnosticBox {
    background-color: $danger_bg;
    border: 1px solid $danger;
    border-radius: 12px;
}
#statusBannerAbandoned {
    background-color: $abandoned_bg;
    border: 1px solid $abandoned;
}
#statusBannerCancelled {
    background-color: $raised;
    border: 1px solid $border;
}
#statusSuccess, #statusWarning, #statusFailed, #statusAbandoned, #statusCancelled {
    font-size: 16px;
    font-weight: 700;
}
#statusSuccess { color: $success; }
#statusWarning { color: $warning; }
#statusFailed { color: $danger; }
#statusAbandoned { color: $abandoned; }
#statusCancelled { color: $muted; }
#diagnosticTitle {
    color: $danger;
    font-size: 14px;
    font-weight: 700;
}
#diagnosticReason {
    color: $danger;
    font-size: 13px;
    font-weight: 600;
}
#diagnosticAdvice {
    color: $danger;
    font-size: 12px;
}

/* macOS 27 Window Controls & Frosted Chrome Enhancements */
#macTrafficLights {
    spacing: 8px;
}
.trafficDot {
    border-radius: 6px;
    border: none;
}
.trafficClose { background-color: #FF5F56; }
.trafficMinimize { background-color: #FFBD2E; }
.trafficZoom { background-color: #27C93F; }
""")


def build_stylesheet(theme: str) -> str:
    return STYLE_TEMPLATE.substitute(THEMES[theme])


def read_theme(settings: QtCore.QSettings) -> str:
    value = settings.value("appearance/theme", "dark")
    return value if isinstance(value, str) and value in THEMES else "dark"


def theme_color(token: str) -> QtGui.QColor:
    application = QtWidgets.QApplication.instance()
    theme = application.property("testboxTheme") if application else "dark"
    return QtGui.QColor(THEMES.get(theme, THEMES["dark"])[token])


def apply_theme(application: QtWidgets.QApplication, theme: str) -> None:
    colors = THEMES[theme]
    if application.property("testboxTheme") is None:
        application.setStyle("Fusion")
    palette = QtGui.QPalette()
    roles = {
        "Window": "window", "WindowText": "text", "Base": "input", "AlternateBase": "panel",
        "Text": "text", "Button": "raised", "ButtonText": "text", "ToolTipBase": "raised",
        "ToolTipText": "text", "Highlight": "selection", "HighlightedText": "on_selection",
        "Link": "accent", "LinkVisited": "info", "PlaceholderText": "muted", "Mid": "border",
        "Light": "hover", "Dark": "input", "Shadow": "border", "BrightText": "text",
    }
    for role, token in roles.items():
        palette.setColor(getattr(QtGui.QPalette.ColorRole, role), QtGui.QColor(colors[token]))
    for role in ("WindowText", "Text", "ButtonText", "PlaceholderText"):
        palette.setColor(QtGui.QPalette.ColorGroup.Disabled, getattr(QtGui.QPalette.ColorRole, role), QtGui.QColor(colors["disabled"]))
    application.setProperty("testboxTheme", theme)
    application.setPalette(palette)
    application.setStyleSheet(build_stylesheet(theme))
    status_tokens = {"SUCCEEDED": "success", "FAILED": "danger", "ABANDONED": "abandoned",
                     "RUNNING": "info", "PENDING": "info"}
    for widget in application.allWidgets():
        if isinstance(widget, QtWidgets.QTableWidget):
            for row in range(widget.rowCount()):
                for column in range(widget.columnCount()):
                    item = widget.item(row, column)
                    status = item.data(QtCore.Qt.ItemDataRole.UserRole) if item else None
                    if widget.objectName() == "taskHistoryTable" and isinstance(status, str):
                        token = status_tokens.get(status, "muted")
                        item.setForeground(QtGui.QColor(colors[token]))
        widget.update()
