# To-Do Graph App (Windows, Offline)

https://youtu.be/dRnIKnhPrVg?si=hCx7hWrIt0GpZmBJ

Minimal local task manager with immediate atomic persistence and graph visualization.

This application was first targetted at my personal goals. You should carefully read this file if you want to run my work.

<img width="2023" height="1188" alt="image" src="https://github.com/user-attachments/assets/69f218b0-8eda-4c87-a83a-56f2126a6999" />

WHY IT IS DIFFERENT

-GOOD FONTS 
-GRAPH VIEW/VISUALISATION
-INFINITE SUBTASKS
-MINIMAL
-NO SAVE BUTTON

## Features

- No save button. Every mutation writes immediately to disk.
- Infinite nesting with graph-style parent/child structure.
- Per-task stable coordinates (`x`, `y`) persisted.
- Parent time auto-updates recursively:
  - Parent `time` = sum of direct children `time`.
- Completion scheme:
  - First check = completed.
  - Second click = recursive delete.
  - Parent completion is independent (no auto parent completion/deletion).
- Leaf-only statistics:
  - Only completed leaf tasks count.
  - Tracks count + total completed leaf minutes.
- Graph View:
  - Coordinate plane `[-1, 1]`.
  - Gray points for all tasks.
  - Light gray parent-child connection lines.
  - Realtime sync with task changes.
- Settings modal:
  - Font size (`small` / `medium` / `large`) with realtime preview.
  - Persists and auto-loads on startup.
- Optional desktop shortcut + Windows startup shortcut.

## Data Model

`data/tasks.json`

```json
{
  "tasks": [
    {
      "id": "uuid",
      "title": "string",
      "parent_id": null,
      "children": [],
      "time": 30,
      "completed": false,
      "x": 0.25,
      "y": -0.4,
      "custom_tags": "#exam #math"
    }
  ]
}
```

`data/settings.json`

```json
{
  "first_launch_prompted": false,
  "startup_enabled": false,
  "font_size": "medium",
  "stats": {
    "completed_leaf_count": 0,
    "completed_leaf_minutes": 0,
    "completed_leaf_ids": []
  }
}
```

## Atomic Persistence

All writes use:

1. write `*.tmp`
2. `flush()` + `fsync()`
3. `os.replace(tmp, real_file)`

## Run

```powershell
cd /d "path\to\to_do_list"
py -3.11 -m venv .venv
.\.venv\Scripts\activate.bat
pip install -r requirements.txt
python main.py
```

## Build EXE

```powershell
python -m PyInstaller --noconfirm --windowed --name TodoList --add-data "data;data" --add-data "fonts;fonts" main.py
```

Output:

- `dist\TodoList\TodoList.exe`

Important for sharing:

- Share the whole `dist\TodoList` folder, not only `TodoList.exe`.
