"""Compatibility entry point for the current editor's isolated WPF test."""
from pathlib import Path
current=Path(__file__).with_name('preview_ventilation_editor.py')
exec(compile(current.read_text(encoding='utf8'),str(current),'exec'))
