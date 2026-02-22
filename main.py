
import json
import os
import subprocess
import sys
import uuid
from copy import deepcopy
from pathlib import Path
from typing import Any, Callable, Optional

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QFontDatabase, QFontMetrics, QKeySequence, QPainter, QPen, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QComboBox,
    QDialog,
    QFormLayout,
    QHeaderView,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QSpinBox,
    QToolTip,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)


FONT_SIZE_MAP: dict[str, int] = {
    "small": 13,
    "medium": 15,
    "large": 18,
}
DEFAULT_FONT_SIZE = "medium"


def normalize_custom_tags(raw: str) -> str:
    parts = [p.strip() for p in raw.replace(",", " ").split() if p.strip()]
    if not parts:
        return ""
    return " ".join(p if p.startswith("#") else f"#{p}" for p in parts)


def task_display_text(task: dict[str, Any]) -> str:
    title = str(task.get("title", "Untitled task"))
    minutes = int(task.get("time", 0))
    tags = str(task.get("custom_tags", "")).strip()
    suffix = f" {tags}" if tags else ""
    return f"{title}  #{minutes}min{suffix}"


def is_leaf(task: dict[str, Any]) -> bool:
    return len(task.get("children", [])) == 0


def flatten_tasks(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    points: list[dict[str, Any]] = []

    def walk(nodes: list[dict[str, Any]]) -> None:
        for node in nodes:
            points.append(
                {
                    "id": str(node.get("id", "")),
                    "title": str(node.get("title", "")),
                    "parent_id": node.get("parent_id"),
                    "time": int(node.get("time", 0)),
                    "completed": bool(node.get("completed", False)),
                    "x": float(node.get("x", 0.0)),
                    "y": float(node.get("y", 0.0)),
                    "custom_tags": str(node.get("custom_tags", "")),
                }
            )
            walk(node.get("children", []))

    walk(tasks)
    return points


class JsonStore:
    def __init__(self, data_dir: Path) -> None:
        self.data_dir = data_dir
        self.tasks_path = data_dir / "tasks.json"
        self.settings_path = data_dir / "settings.json"
        self.data_dir.mkdir(parents=True, exist_ok=True)
        if not self.tasks_path.exists():
            self.atomic_write(self.tasks_path, {"tasks": []})
        if not self.settings_path.exists():
            self.atomic_write(self.settings_path, self.default_settings())

    @staticmethod
    def default_settings() -> dict[str, Any]:
        return {
            "first_launch_prompted": False,
            "startup_enabled": False,
            "font_size": DEFAULT_FONT_SIZE,
            "stats": {
                "completed_leaf_count": 0,
                "completed_leaf_minutes": 0,
                "completed_leaf_ids": [],
            },
        }

    def load_tasks(self) -> list[dict[str, Any]]:
        with self.tasks_path.open("r", encoding="utf-8") as f:
            raw = json.load(f)
        if isinstance(raw, dict) and isinstance(raw.get("tasks"), list):
            return raw["tasks"]
        if isinstance(raw, list):
            return raw
        raise ValueError("tasks.json must be {'tasks': [...]} or [...]")

    def load_settings(self) -> dict[str, Any]:
        with self.settings_path.open("r", encoding="utf-8") as f:
            raw = json.load(f)
        if not isinstance(raw, dict):
            return self.default_settings()
        merged = self.default_settings()
        merged.update(raw)
        if merged.get("font_size") not in FONT_SIZE_MAP:
            merged["font_size"] = DEFAULT_FONT_SIZE
        stats = merged.get("stats", {})
        if not isinstance(stats, dict):
            stats = {}
        merged["stats"] = {
            "completed_leaf_count": int(stats.get("completed_leaf_count", 0)),
            "completed_leaf_minutes": int(stats.get("completed_leaf_minutes", 0)),
            "completed_leaf_ids": list(stats.get("completed_leaf_ids", [])),
        }
        return merged

    def atomic_write(self, path: Path, payload: dict[str, Any]) -> None:
        temp_path = path.with_suffix(path.suffix + ".tmp")
        with temp_path.open("w", encoding="utf-8") as f:
            json.dump(payload, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(temp_path, path)

    def save_tasks(self, tasks: list[dict[str, Any]]) -> None:
        self.atomic_write(self.tasks_path, {"tasks": tasks})

    def save_settings(self, settings: dict[str, Any]) -> None:
        self.atomic_write(self.settings_path, settings)


def normalize_task_node(raw: dict[str, Any], parent_id: Optional[str]) -> tuple[dict[str, Any], bool]:
    changed = False

    task_id = str(raw.get("id") or uuid.uuid4())
    if raw.get("id") != task_id:
        changed = True

    title = str(raw.get("title") or raw.get("text") or "Untitled task")
    if "title" not in raw or raw.get("title") != title:
        changed = True

    completed = bool(raw.get("completed", False))
    if "completed" not in raw:
        completed = bool(int(raw.get("completed_state", 0)) == 1)
        changed = True

    if "time" in raw:
        time_value = int(raw.get("time", 0))
    else:
        time_value = int(raw.get("estimated_minutes", 0))
        changed = True

    if "x" in raw:
        x_val = float(raw.get("x", 0.0))
    else:
        x_val = float(raw.get("coord", {}).get("x", 0.0))
        changed = True

    if "y" in raw:
        y_val = float(raw.get("y", 0.0))
    else:
        y_val = float(raw.get("coord", {}).get("y", 0.0))
        changed = True

    custom_tags = str(raw.get("custom_tags", ""))
    if "custom_tags" not in raw:
        changed = True

    children_raw = raw.get("children", [])
    if not isinstance(children_raw, list):
        children_raw = []
        changed = True

    children_norm: list[dict[str, Any]] = []
    for child in children_raw:
        if isinstance(child, dict):
            normalized_child, child_changed = normalize_task_node(child, task_id)
            children_norm.append(normalized_child)
            if child_changed:
                changed = True
        else:
            changed = True

    normalized = {
        "id": task_id,
        "title": title,
        "parent_id": parent_id,
        "children": children_norm,
        "time": time_value,
        "completed": completed,
        "x": x_val,
        "y": y_val,
        "custom_tags": custom_tags,
    }

    return normalized, True if changed else (
        raw.get("parent_id") != parent_id
        or "title" not in raw
        or "time" not in raw
        or "completed" not in raw
        or "x" not in raw
        or "y" not in raw
    )


def recompute_parent_time(task: dict[str, Any]) -> int:
    children = task.get("children", [])
    for child in children:
        recompute_parent_time(child)
    if children:
        task["time"] = sum(int(child.get("time", 0)) for child in children)
    else:
        task["time"] = int(task.get("time", 0))
    return int(task["time"])


def normalize_tree(tasks: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], bool]:
    normalized_roots: list[dict[str, Any]] = []
    changed = False
    for raw in tasks:
        if not isinstance(raw, dict):
            changed = True
            continue
        normalized, node_changed = normalize_task_node(raw, None)
        normalized_roots.append(normalized)
        changed = changed or node_changed

    for root in normalized_roots:
        recompute_parent_time(root)
    return normalized_roots, changed


def rebind_parent_ids_and_recompute(tasks: list[dict[str, Any]]) -> None:
    def walk(nodes: list[dict[str, Any]], pid: Optional[str]) -> None:
        for node in nodes:
            node["parent_id"] = pid
            walk(node.get("children", []), str(node.get("id", "")))

    walk(tasks, None)
    for root in tasks:
        recompute_parent_time(root)

class CoordinatePicker(QWidget):
    coord_changed = Signal(float, float)

    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.selected: Optional[tuple[float, float]] = None
        self.setMinimumSize(280, 280)

    def mousePressEvent(self, event) -> None:  # type: ignore[override]
        if event.button() != Qt.LeftButton:
            return
        margin = 24.0
        side = max(10.0, min(float(self.width()), float(self.height())) - margin * 2)
        half = side / 2.0
        center_x = float(self.width()) / 2.0
        center_y = float(self.height()) / 2.0

        x = (event.position().x() - center_x) / half
        y = -((event.position().y() - center_y) / half)
        x = max(-1.0, min(1.0, x))
        y = max(-1.0, min(1.0, y))

        self.selected = (round(x, 4), round(y, 4))
        self.coord_changed.emit(self.selected[0], self.selected[1])
        self.update()
        super().mousePressEvent(event)

    def paintEvent(self, _event) -> None:  # type: ignore[override]
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor("#eef0f1"))

        margin = 24.0
        side = max(10.0, min(float(self.width()), float(self.height())) - margin * 2)
        left = (float(self.width()) - side) / 2.0
        top = (float(self.height()) - side) / 2.0
        axis_rect = QRectF(left, top, side, side)
        center = QPointF(axis_rect.center().x(), axis_rect.center().y())

        p.setPen(QPen(QColor("#99a2a8"), 1))
        p.drawRect(axis_rect)
        p.setPen(QPen(QColor("#7d868c"), 1))
        p.drawLine(QPointF(axis_rect.left(), center.y()), QPointF(axis_rect.right(), center.y()))
        p.drawLine(QPointF(center.x(), axis_rect.top()), QPointF(center.x(), axis_rect.bottom()))

        p.setPen(QPen(QColor("#7b848a"), 1))
        p.drawText(int(axis_rect.right() - 66), int(center.y() - 12), "Important")
        p.save()
        p.translate(int(center.x() + 12), int(axis_rect.top() + 62))
        p.rotate(-90)
        p.drawText(0, 0, "Urgent")
        p.restore()

        if self.selected is not None:
            x, y = self.selected
            px = center.x() + x * (axis_rect.width() / 2.0)
            py = center.y() - y * (axis_rect.height() / 2.0)
            p.setPen(QPen(QColor("#5f666b"), 2))
            p.drawLine(QPointF(px - 6, py - 6), QPointF(px + 6, py + 6))
            p.drawLine(QPointF(px - 6, py + 6), QPointF(px + 6, py - 6))


class TaskDialog(QDialog):
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Create Task")
        self.setModal(True)
        self.task_payload: Optional[dict[str, Any]] = None

        self.title_input = QLineEdit()
        self.title_input.setPlaceholderText("Task title")

        self.time_input = QSpinBox()
        self.time_input.setRange(0, 100000)
        self.time_input.setValue(30)
        self.time_input.setSuffix(" min")

        self.tags_input = QLineEdit()
        self.tags_input.setPlaceholderText("Optional tags, e.g. exam math")

        self.coord_picker = CoordinatePicker()
        self.coord_label = QLabel("Coordinate: Not selected")

        form = QFormLayout()
        form.addRow("Title", self.title_input)
        form.addRow("Time", self.time_input)
        form.addRow("Custom Tags", self.tags_input)

        buttons = QHBoxLayout()
        save_btn = QPushButton("Save")
        cancel_btn = QPushButton("Cancel")
        buttons.addWidget(save_btn)
        buttons.addWidget(cancel_btn)

        root = QVBoxLayout()
        root.addLayout(form)
        root.addWidget(QLabel("Click the axis to choose a point (-1 to 1):"))
        root.addWidget(self.coord_picker)
        root.addWidget(self.coord_label)
        root.addLayout(buttons)
        self.setLayout(root)

        self.coord_picker.coord_changed.connect(self._on_coord_changed)
        save_btn.clicked.connect(self._on_save)
        cancel_btn.clicked.connect(self.reject)

    def _on_coord_changed(self, x: float, y: float) -> None:
        self.coord_label.setText(f"Coordinate: ({x:.4f}, {y:.4f})")

    def _on_save(self) -> None:
        title = self.title_input.text().strip()
        if not title:
            QMessageBox.warning(self, "Incomplete Input", "Task title cannot be empty.")
            return
        if self.coord_picker.selected is None:
            QMessageBox.warning(self, "Incomplete Input", "You must select a point on the axis.")
            return

        x, y = self.coord_picker.selected
        self.task_payload = {
            "id": str(uuid.uuid4()),
            "title": title,
            "parent_id": None,
            "children": [],
            "time": int(self.time_input.value()),
            "completed": False,
            "x": x,
            "y": y,
            "custom_tags": normalize_custom_tags(self.tags_input.text()),
        }
        self.accept()


class FontSettingsDialog(QDialog):
    def __init__(
        self,
        current_mode: str,
        current_family: str,
        on_change: Callable[[str], None],
        parent: Optional[QWidget] = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.setModal(True)
        self._on_change = on_change

        self.mode_combo = QComboBox()
        self.mode_combo.addItem("Small", "small")
        self.mode_combo.addItem("Medium", "medium")
        self.mode_combo.addItem("Large", "large")

        idx = {"small": 0, "medium": 1, "large": 2}.get(current_mode, 1)
        self.mode_combo.setCurrentIndex(idx)

        self.preview = QLabel("Preview: The quick brown fox jumps over the lazy dog.")
        preview_font = QFont(current_family, FONT_SIZE_MAP.get(current_mode, FONT_SIZE_MAP[DEFAULT_FONT_SIZE]))
        self.preview.setFont(preview_font)

        close_btn = QPushButton("Close")

        form = QFormLayout()
        form.addRow("Font Size", self.mode_combo)

        root = QVBoxLayout()
        root.addLayout(form)
        root.addWidget(self.preview)
        root.addWidget(close_btn)
        self.setLayout(root)

        self.mode_combo.currentIndexChanged.connect(self._on_mode_changed)
        close_btn.clicked.connect(self.accept)

    def _on_mode_changed(self) -> None:
        mode = str(self.mode_combo.currentData())
        self._on_change(mode)
        size = FONT_SIZE_MAP.get(mode, FONT_SIZE_MAP[DEFAULT_FONT_SIZE])
        font = self.preview.font()
        font.setPointSize(size)
        self.preview.setFont(font)


class GraphCanvas(QWidget):
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.points: list[dict[str, Any]] = []
        self.setMouseTracking(True)
        self._hide_timer = QTimer(self)
        self._hide_timer.setInterval(350)
        self._hide_timer.setSingleShot(True)
        self._hide_timer.timeout.connect(QToolTip.hideText)

    def set_tasks(self, tasks: list[dict[str, Any]]) -> None:
        self.points = flatten_tasks(tasks)
        self.update()

    def _plot_rect(self) -> QRectF:
        margin = 28.0
        side = max(10.0, min(float(self.width()), float(self.height())) - margin * 2)
        left = (float(self.width()) - side) / 2.0
        top = (float(self.height()) - side) / 2.0
        return QRectF(left, top, side, side)

    def _dot_radius(self, minutes: int, max_minutes: int) -> float:
        if max_minutes <= 0:
            return 3.0
        norm = max(0.0, min(1.0, minutes / max_minutes))
        return 2.8 + norm * 2.2

    def _dot_color(self, minutes: int, max_minutes: int) -> QColor:
        if max_minutes <= 0:
            return QColor(130, 130, 130)
        norm = max(0.0, min(1.0, minutes / max_minutes))
        shade = int(170 - norm * 90)
        shade = max(70, min(170, shade))
        return QColor(shade, shade, shade)

    def paintEvent(self, _event) -> None:  # type: ignore[override]
        p = QPainter(self)
        p.setRenderHint(QPainter.Antialiasing)
        p.fillRect(self.rect(), QColor("#eceff1"))

        plot = self._plot_rect()
        center = QPointF(plot.center().x(), plot.center().y())

        p.setPen(QPen(QColor("#9aa3a9"), 1))
        p.drawRect(plot)
        p.drawLine(QPointF(plot.left(), center.y()), QPointF(plot.right(), center.y()))
        p.drawLine(QPointF(center.x(), plot.top()), QPointF(center.x(), plot.bottom()))

        p.setPen(QPen(QColor("#7b848a"), 1))
        p.drawText(int(plot.right() - 66), int(center.y() - 12), "Important")
        p.save()
        p.translate(int(center.x() + 12), int(plot.top() + 62))
        p.rotate(-90)
        p.drawText(0, 0, "Urgent")
        p.restore()

        coord_by_id: dict[str, QPointF] = {}
        for pt in self.points:
            px = center.x() + max(-1.0, min(1.0, pt["x"])) * (plot.width() / 2.0)
            py = center.y() - max(-1.0, min(1.0, pt["y"])) * (plot.height() / 2.0)
            coord_by_id[pt["id"]] = QPointF(px, py)

        p.setPen(QPen(QColor(170, 176, 181), 1))
        for pt in self.points:
            parent_id = pt.get("parent_id")
            if parent_id and parent_id in coord_by_id and pt["id"] in coord_by_id:
                p.drawLine(coord_by_id[parent_id], coord_by_id[pt["id"]])

        max_minutes = max((int(pt["time"]) for pt in self.points), default=0)
        p.setPen(Qt.NoPen)
        for pt in self.points:
            pos = coord_by_id[pt["id"]]
            p.setBrush(self._dot_color(int(pt["time"]), max_minutes))
            radius = self._dot_radius(int(pt["time"]), max_minutes)
            p.drawEllipse(pos, radius, radius)

    def mouseMoveEvent(self, event) -> None:  # type: ignore[override]
        if not self.points:
            QToolTip.hideText()
            return

        plot = self._plot_rect()
        center = QPointF(plot.center().x(), plot.center().y())
        mx = event.position().x()
        my = event.position().y()

        closest: Optional[tuple[dict[str, Any], float]] = None
        for pt in self.points:
            px = center.x() + max(-1.0, min(1.0, pt["x"])) * (plot.width() / 2.0)
            py = center.y() - max(-1.0, min(1.0, pt["y"])) * (plot.height() / 2.0)
            d2 = (mx - px) ** 2 + (my - py) ** 2
            if closest is None or d2 < closest[1]:
                closest = (pt, d2)

        if closest is not None and closest[1] <= 11.0**2:
            point = closest[0]
            tags = f" {point['custom_tags']}" if point.get("custom_tags") else ""
            msg = f"{point['title']}\n{int(point['time'])} min{tags}"
            QToolTip.showText(event.globalPosition().toPoint(), msg, self)
            self._hide_timer.start()
        else:
            QToolTip.hideText()


class GraphWindow(QMainWindow):
    def __init__(self, parent: Optional[QWidget] = None) -> None:
        super().__init__(parent)
        self.setWindowTitle("Graph View")
        self.canvas = GraphCanvas()

        root = QVBoxLayout()
        root.addWidget(self.canvas)

        container = QWidget()
        container.setLayout(root)
        self.setCentralWidget(container)

        screen = QApplication.primaryScreen()
        if screen is not None:
            geo = screen.availableGeometry()
            self.resize(max(380, int(geo.width() * 0.5)), max(320, int(geo.height() * 0.5)))
        else:
            self.resize(560, 420)

    def set_tasks(self, tasks: list[dict[str, Any]]) -> None:
        self.canvas.set_tasks(tasks)

class TodoMainWindow(QMainWindow):
    TASK_ID_ROLE = Qt.UserRole + 1

    def __init__(self, store: JsonStore) -> None:
        super().__init__()
        self.store = store
        self.tasks: list[dict[str, Any]] = []
        self.settings: dict[str, Any] = {}
        self.undo_last_deleted: Optional[dict[str, Any]] = None
        self.graph_window: Optional[GraphWindow] = None
        self._suppress_item_changed = False

        self.setWindowTitle("Local To-Do List")
        self.resize(980, 700)

        self.tree = QTreeWidget()
        self.tree.setColumnCount(1)
        self.tree.setHeaderHidden(True)
        self.tree.setIndentation(20)
        self.tree.setWordWrap(False)
        self.tree.setTextElideMode(Qt.ElideNone)
        self.tree.setUniformRowHeights(True)
        self.tree.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)
        header = self.tree.header()
        header.setSectionResizeMode(0, QHeaderView.Interactive)
        self.tree.setColumnWidth(0, 900)
        self.tree.itemChanged.connect(self._on_item_changed)

        self.new_task_btn = QPushButton("New Task")
        self.graph_btn = QPushButton("Graph View")
        self.settings_btn = QPushButton("Settings")
        self.expand_btn = QPushButton("Expand All")
        self.collapse_btn = QPushButton("Collapse All")
        self.undo_btn = QPushButton("Undo Delete")
        self.stats_btn = QPushButton("Statistics")
        self.reset_stats_btn = QPushButton("Reset Stats")
        self.startup_btn = QPushButton("Startup: Off")

        self.new_task_btn.clicked.connect(lambda: self._create_task(None))
        self.graph_btn.clicked.connect(self._open_graph_view)
        self.settings_btn.clicked.connect(self._open_settings)
        self.expand_btn.clicked.connect(self.tree.expandAll)
        self.collapse_btn.clicked.connect(self.tree.collapseAll)
        self.undo_btn.clicked.connect(self._on_undo_delete)
        self.stats_btn.clicked.connect(self._show_statistics)
        self.reset_stats_btn.clicked.connect(self._reset_statistics)
        self.startup_btn.clicked.connect(self._toggle_startup)

        self.ctrl_o_shortcut = QShortcut(QKeySequence("Ctrl+O"), self)
        self.ctrl_o_shortcut.activated.connect(lambda: self._create_task(None))
        self.ctrl_z_shortcut = QShortcut(QKeySequence("Ctrl+Z"), self)
        self.ctrl_z_shortcut.activated.connect(self._on_undo_delete)
        self.enter_shortcut = QShortcut(QKeySequence("Return"), self.tree)
        self.enter_shortcut.activated.connect(self._create_subtask_from_current)
        self.numpad_enter_shortcut = QShortcut(QKeySequence("Enter"), self.tree)
        self.numpad_enter_shortcut.activated.connect(self._create_subtask_from_current)

        hint = QLabel(
            "Rule: First click marks completed. Second click removes the task and all subtasks."
        )
        hint.setObjectName("hint")

        top_buttons = QHBoxLayout()
        top_buttons.addWidget(self.new_task_btn)
        top_buttons.addWidget(self.graph_btn)
        top_buttons.addWidget(self.settings_btn)
        top_buttons.addWidget(self.undo_btn)
        top_buttons.addWidget(self.stats_btn)
        top_buttons.addWidget(self.reset_stats_btn)
        top_buttons.addWidget(self.startup_btn)
        top_buttons.addWidget(self.expand_btn)
        top_buttons.addWidget(self.collapse_btn)
        top_buttons.addStretch(1)

        body = QVBoxLayout()
        title = QLabel("To-Do List")
        title.setObjectName("title")
        body.addWidget(title)
        body.addLayout(top_buttons)
        body.addWidget(self.tree)
        body.addWidget(hint)

        container = QWidget()
        container.setLayout(body)
        self.setCentralWidget(container)

        self._apply_style()
        self._load_state()
        self._apply_font_size(self.settings.get("font_size", DEFAULT_FONT_SIZE), persist=False)
        self._update_startup_button()
        self._rebuild_tree()
        self._handle_first_launch_shortcut_prompt()

    def _apply_style(self) -> None:
        self.setStyleSheet(
            """
            QWidget {
                background: #eceff1;
                color: #2d3236;
                font-size: 15px;
            }
            QTreeWidget {
                background: #f4f6f7;
                border: 1px solid #c5ccd1;
                border-radius: 6px;
                padding: 6px;
            }
            QTreeWidget::indicator:unchecked {
                border: 1px solid #c3c9ce;
                background: #eceff1;
                width: 14px;
                height: 14px;
            }
            QTreeWidget::indicator:checked {
                border: 1px solid #a1a8ad;
                background: #d4d9dd;
                width: 14px;
                height: 14px;
            }
            QPushButton {
                background: #dfe5e8;
                border: 1px solid #bcc4c9;
                border-radius: 6px;
                padding: 6px 10px;
            }
            QPushButton:hover {
                background: #d4dbe0;
            }
            QLabel#title {
                font-size: 24px;
                padding-bottom: 8px;
            }
            QLabel#hint {
                color: #697279;
                padding-top: 4px;
            }
            """
        )

    def _load_state(self) -> None:
        try:
            raw_tasks = self.store.load_tasks()
            self.tasks, changed = normalize_tree(raw_tasks)
            if changed:
                self.store.save_tasks(self.tasks)
        except Exception as exc:
            QMessageBox.critical(self, "Load Error", f"Failed to load tasks.json:\n{exc}")
            self.tasks = []

        try:
            self.settings = self.store.load_settings()
        except Exception as exc:
            QMessageBox.critical(self, "Load Error", f"Failed to load settings.json:\n{exc}")
            self.settings = self.store.default_settings()

    def _persist_tasks(self) -> bool:
        try:
            self.store.save_tasks(self.tasks)
            self._sync_graph_view()
            return True
        except Exception as exc:
            QMessageBox.critical(self, "Write Error", f"Failed to write tasks to disk:\n{exc}")
            return False

    def _persist_settings(self) -> bool:
        try:
            self.store.save_settings(self.settings)
            return True
        except Exception as exc:
            QMessageBox.critical(self, "Write Error", f"Failed to write settings to disk:\n{exc}")
            return False

    def _rebuild_tree(self) -> None:
        self._suppress_item_changed = True
        self.tree.clear()
        for task in self.tasks:
            self.tree.addTopLevelItem(self._build_item(task))
        self._update_text_column_width()
        self.tree.expandAll()
        self._suppress_item_changed = False

    def _build_item(self, task: dict[str, Any]) -> QTreeWidgetItem:
        text = task_display_text(task)
        item = QTreeWidgetItem([text])
        item.setFlags(item.flags() | Qt.ItemIsUserCheckable | Qt.ItemIsSelectable | Qt.ItemIsEnabled)
        item.setToolTip(0, text)
        item.setData(0, self.TASK_ID_ROLE, task.get("id", ""))
        item.setCheckState(0, Qt.Checked if bool(task.get("completed", False)) else Qt.Unchecked)
        self._apply_item_visual(item, bool(task.get("completed", False)))
        for child in task.get("children", []):
            item.addChild(self._build_item(child))
        return item

    def _update_text_column_width(self) -> None:
        metrics = QFontMetrics(self.tree.font())
        max_width = self.tree.viewport().width()
        stack: list[QTreeWidgetItem] = [self.tree.topLevelItem(i) for i in range(self.tree.topLevelItemCount())]
        while stack:
            item = stack.pop()
            if item is None:
                continue
            text_width = metrics.horizontalAdvance(item.text(0))
            max_width = max(max_width, text_width + 70)
            for i in range(item.childCount()):
                stack.append(item.child(i))
        self.tree.setColumnWidth(0, max_width)

    def _apply_item_visual(self, item: QTreeWidgetItem, completed: bool) -> None:
        font = item.font(0)
        font.setStrikeOut(completed)
        item.setFont(0, font)
        item.setForeground(0, QColor("#7f878d") if completed else QColor("#2f3438"))

    def _find_task_ref(
        self, task_id: str
    ) -> Optional[tuple[list[dict[str, Any]], int, dict[str, Any], Optional[str]]]:
        return self._find_task_ref_in_list(self.tasks, task_id, None)

    def _find_task_ref_in_list(
        self,
        tasks: list[dict[str, Any]],
        task_id: str,
        parent_id: Optional[str],
    ) -> Optional[tuple[list[dict[str, Any]], int, dict[str, Any], Optional[str]]]:
        for idx, task in enumerate(tasks):
            current_id = str(task.get("id", ""))
            if current_id == task_id:
                return tasks, idx, task, parent_id
            found = self._find_task_ref_in_list(task.get("children", []), task_id, current_id)
            if found is not None:
                return found
        return None

    def _create_task(self, parent_task_id: Optional[str]) -> None:
        dialog = TaskDialog(self)
        if dialog.exec() != QDialog.Accepted or dialog.task_payload is None:
            return

        task = dialog.task_payload
        if parent_task_id is None:
            self.tasks.append(task)
        else:
            ref = self._find_task_ref(parent_task_id)
            if ref is None:
                QMessageBox.critical(self, "Error", "Parent task was not found.")
                return
            _, _, parent_task, _ = ref
            parent_task.setdefault("children", []).append(task)

        rebind_parent_ids_and_recompute(self.tasks)
        if not self._persist_tasks():
            return
        self._rebuild_tree()

    def _on_item_changed(self, item: QTreeWidgetItem, column: int) -> None:
        if self._suppress_item_changed or column != 0:
            return

        task_id = str(item.data(0, self.TASK_ID_ROLE))
        ref = self._find_task_ref(task_id)
        if ref is None:
            return

        task_list, idx, task, parent_id = ref
        checked_now = item.checkState(0) == Qt.Checked
        current_state = bool(task.get("completed", False))

        if not current_state and checked_now:
            task["completed"] = True
            self._record_leaf_completion_stats(task)
            if not self._persist_tasks():
                task["completed"] = False
                self._suppress_item_changed = True
                item.setCheckState(0, Qt.Unchecked)
                self._apply_item_visual(item, False)
                self._suppress_item_changed = False
                return
            self._apply_item_visual(item, True)
            return

        if current_state and not checked_now:
            deleted_snapshot = deepcopy(task)
            self.undo_last_deleted = {
                "parent_id": parent_id,
                "index": idx,
                "task": deleted_snapshot,
            }
            task_list.pop(idx)
            rebind_parent_ids_and_recompute(self.tasks)
            if not self._persist_tasks():
                task_list.insert(idx, deleted_snapshot)
                rebind_parent_ids_and_recompute(self.tasks)
                self.undo_last_deleted = None
                self._suppress_item_changed = True
                item.setCheckState(0, Qt.Checked)
                self._apply_item_visual(item, True)
                self._suppress_item_changed = False
                return

            self._suppress_item_changed = True
            parent_item = item.parent()
            if parent_item is None:
                top_index = self.tree.indexOfTopLevelItem(item)
                self.tree.takeTopLevelItem(top_index)
            else:
                parent_item.removeChild(item)
            self._suppress_item_changed = False
            return

        self._suppress_item_changed = True
        item.setCheckState(0, Qt.Checked if current_state else Qt.Unchecked)
        self._apply_item_visual(item, current_state)
        self._suppress_item_changed = False

    def _on_undo_delete(self) -> None:
        if not self.undo_last_deleted:
            return

        payload = deepcopy(self.undo_last_deleted)
        parent_id = payload.get("parent_id")
        index = int(payload.get("index", 0))
        task = payload.get("task")
        if not isinstance(task, dict):
            self.undo_last_deleted = None
            return

        if parent_id is None:
            insert_at = max(0, min(index, len(self.tasks)))
            self.tasks.insert(insert_at, task)
        else:
            parent_ref = self._find_task_ref(str(parent_id))
            if parent_ref is None:
                insert_at = max(0, min(index, len(self.tasks)))
                self.tasks.insert(insert_at, task)
            else:
                _, _, parent_task, _ = parent_ref
                children = parent_task.setdefault("children", [])
                insert_at = max(0, min(index, len(children)))
                children.insert(insert_at, task)

        rebind_parent_ids_and_recompute(self.tasks)
        if not self._persist_tasks():
            return
        self.undo_last_deleted = None
        self._rebuild_tree()

    def _record_leaf_completion_stats(self, task: dict[str, Any]) -> None:
        if not is_leaf(task):
            return
        stats = self.settings.setdefault("stats", {})
        ids = list(stats.get("completed_leaf_ids", []))
        task_id = str(task.get("id", ""))
        if task_id in ids:
            return
        ids.append(task_id)
        stats["completed_leaf_ids"] = ids
        stats["completed_leaf_count"] = int(stats.get("completed_leaf_count", 0)) + 1
        stats["completed_leaf_minutes"] = int(stats.get("completed_leaf_minutes", 0)) + int(task.get("time", 0))
        self._persist_settings()

    def _show_statistics(self) -> None:
        stats = self.settings.get("stats", {})
        count = int(stats.get("completed_leaf_count", 0))
        minutes = int(stats.get("completed_leaf_minutes", 0))
        QMessageBox.information(
            self,
            "Statistics",
            f"Completed leaf count: {count}\nCompleted leaf minutes: {minutes} min",
        )

    def _reset_statistics(self) -> None:
        answer = QMessageBox.question(
            self,
            "Reset Statistics",
            "Reset all statistics values to zero?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No,
        )
        if answer != QMessageBox.Yes:
            return
        self.settings["stats"] = {
            "completed_leaf_count": 0,
            "completed_leaf_minutes": 0,
            "completed_leaf_ids": [],
        }
        self._persist_settings()

    def _create_subtask_from_current(self) -> None:
        item = self.tree.currentItem()
        if item is None:
            return
        task_id = str(item.data(0, self.TASK_ID_ROLE))
        if not task_id:
            return
        self._create_task(task_id)

    def _open_graph_view(self) -> None:
        if self.graph_window is None:
            self.graph_window = GraphWindow(self)
        self.graph_window.set_tasks(self.tasks)
        self.graph_window.show()
        self.graph_window.raise_()
        self.graph_window.activateWindow()

    def _sync_graph_view(self) -> None:
        if self.graph_window is not None:
            self.graph_window.set_tasks(self.tasks)

    def _apply_font_size(self, mode: str, persist: bool = True) -> None:
        chosen = mode if mode in FONT_SIZE_MAP else DEFAULT_FONT_SIZE
        app = QApplication.instance()
        if app is None:
            return
        font = app.font()
        font.setPointSize(FONT_SIZE_MAP[chosen])
        font.setBold(False)
        font.setItalic(False)
        app.setFont(font)
        if persist:
            self.settings["font_size"] = chosen
            self._persist_settings()
        self._update_text_column_width()

    def _open_settings(self) -> None:
        app = QApplication.instance()
        current_family = app.font().family() if app is not None else "Sans Serif"
        dialog = FontSettingsDialog(
            current_mode=str(self.settings.get("font_size", DEFAULT_FONT_SIZE)),
            current_family=current_family,
            on_change=lambda mode: self._apply_font_size(mode, persist=True),
            parent=self,
        )
        dialog.exec()

    def _shortcut_target_and_args(self) -> tuple[str, str, str]:
        if getattr(sys, "frozen", False):
            target = str(Path(sys.executable).resolve())
            args = ""
            working_dir = str(Path(sys.executable).resolve().parent)
        else:
            target = str(Path(sys.executable).resolve())
            args = f'"{Path(__file__).resolve()}"'
            working_dir = str(Path(__file__).resolve().parent)
        return target, args, working_dir

    def _desktop_shortcut_path(self) -> Path:
        return Path.home() / "Desktop" / "TodoList.lnk"

    def _startup_shortcut_path(self) -> Path:
        appdata = os.environ.get("APPDATA", "")
        return Path(appdata) / "Microsoft" / "Windows" / "Start Menu" / "Programs" / "Startup" / "TodoList.lnk"

    def _create_windows_shortcut(self, link_path: Path) -> tuple[bool, str]:
        if sys.platform != "win32":
            return False, "Shortcut creation is only supported on Windows."

        target, args, working_dir = self._shortcut_target_and_args()
        link_path.parent.mkdir(parents=True, exist_ok=True)

        def esc(s: str) -> str:
            return s.replace("'", "''")

        powershell_script = (
            "$shell = New-Object -ComObject WScript.Shell;"
            f"$sc = $shell.CreateShortcut('{esc(str(link_path))}');"
            f"$sc.TargetPath = '{esc(target)}';"
            f"$sc.Arguments = '{esc(args)}';"
            f"$sc.WorkingDirectory = '{esc(working_dir)}';"
            f"$sc.IconLocation = '{esc(target)},0';"
            "$sc.Save();"
        )

        try:
            subprocess.run(
                [
                    "powershell",
                    "-NoProfile",
                    "-ExecutionPolicy",
                    "Bypass",
                    "-Command",
                    powershell_script,
                ],
                check=True,
                capture_output=True,
                text=True,
            )
            return True, ""
        except Exception as exc:
            return False, str(exc)

    def _remove_shortcut(self, link_path: Path) -> tuple[bool, str]:
        try:
            if link_path.exists():
                link_path.unlink()
            return True, ""
        except Exception as exc:
            return False, str(exc)

    def _handle_first_launch_shortcut_prompt(self) -> None:
        if self.settings.get("first_launch_prompted", False):
            return
        answer = QMessageBox.question(
            self,
            "Create Desktop Shortcut",
            "Create a desktop shortcut for quick access?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.Yes,
        )
        self.settings["first_launch_prompted"] = True
        if answer == QMessageBox.Yes:
            ok, err = self._create_windows_shortcut(self._desktop_shortcut_path())
            if not ok:
                QMessageBox.warning(self, "Shortcut Error", f"Could not create shortcut:\n{err}")
        self._persist_settings()

    def _update_startup_button(self) -> None:
        enabled = bool(self.settings.get("startup_enabled", False))
        self.startup_btn.setText("Startup: On" if enabled else "Startup: Off")

    def _toggle_startup(self) -> None:
        target_state = not bool(self.settings.get("startup_enabled", False))
        startup_link = self._startup_shortcut_path()
        if target_state:
            ok, err = self._create_windows_shortcut(startup_link)
            if not ok:
                QMessageBox.warning(self, "Startup Error", f"Could not enable startup:\n{err}")
                return
            self.settings["startup_enabled"] = True
        else:
            ok, err = self._remove_shortcut(startup_link)
            if not ok:
                QMessageBox.warning(self, "Startup Error", f"Could not disable startup:\n{err}")
                return
            self.settings["startup_enabled"] = False
        if not self._persist_settings():
            return
        self._update_startup_button()


def load_local_font(app: QApplication, fonts_dir: Path) -> None:
    if not fonts_dir.exists():
        return

    preferred_paths = [
        fonts_dir / "PatrickHand-Regular.ttf",
        fonts_dir / "Noto_Sans_SC" / "NotoSansSC-VariableFont_wght.ttf",
        fonts_dir / "Noto_Sans_SC" / "static" / "NotoSansSC-Regular.ttf",
    ]
    ordered_paths: list[Path] = []
    for p in preferred_paths:
        if p.exists() and p not in ordered_paths:
            ordered_paths.append(p)
    if not ordered_paths:
        for p in sorted(fonts_dir.rglob("*.ttf")):
            if p not in ordered_paths:
                ordered_paths.append(p)

    loaded_families: dict[str, str] = {}
    for font_path in ordered_paths:
        font_id = QFontDatabase.addApplicationFont(str(font_path))
        if font_id == -1:
            continue
        families = QFontDatabase.applicationFontFamilies(font_id)
        if families:
            loaded_families[font_path.name] = families[0]

    family = (
        loaded_families.get("PatrickHand-Regular.ttf")
        or loaded_families.get("NotoSansSC-VariableFont_wght.ttf")
        or loaded_families.get("NotoSansSC-Regular.ttf")
        or next(iter(loaded_families.values()), "")
    )
    if not family:
        return

    font = QFont(family, FONT_SIZE_MAP[DEFAULT_FONT_SIZE])
    font.setBold(False)
    font.setItalic(False)
    app.setFont(font)


def storage_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def resource_base_dir() -> Path:
    if getattr(sys, "frozen", False):
        meipass = getattr(sys, "_MEIPASS", "")
        if meipass:
            return Path(meipass).resolve()
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def main() -> None:
    data_dir = storage_base_dir() / "data"
    font_dir = resource_base_dir() / "fonts"
    store = JsonStore(data_dir)

    app = QApplication(sys.argv)
    load_local_font(app, font_dir)
    window = TodoMainWindow(store)
    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    main()
