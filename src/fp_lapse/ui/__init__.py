"""fp-lapse UI — primitives, widgets, and screens.

Pure rendering: each screen produces a `PIL.Image` 320x240 from a
`UIState` (or equivalent). Blitting to hardware is the caller's job
via the `Display` Protocol.
"""

from .edit_screen import (
    EditAction,
    EditScreen,
    EditScreenInteraction,
    EditState,
    editable_fields,
)
from .main_screen import (
    MainAction,
    MainActionResult,
    MainScreen,
    MainScreenInteraction,
    UIState,
    footer_hint,
)
from .manage_menu import (
    MENU_ITEMS,
    ManageMenuAction,
    ManageMenuInteraction,
    ManageMenuState,
    render_manage_menu,
)
from .overlays import (
    OverlayDialog,
    delete_confirm,
    discard_changes,
    handle_overlay_button,
    render_overlay,
    save_confirm,
    stop_confirm,
)

__all__ = [
    "EditAction",
    "EditScreen",
    "EditScreenInteraction",
    "EditState",
    "MENU_ITEMS",
    "MainAction",
    "MainActionResult",
    "MainScreen",
    "MainScreenInteraction",
    "ManageMenuAction",
    "ManageMenuInteraction",
    "ManageMenuState",
    "OverlayDialog",
    "UIState",
    "delete_confirm",
    "discard_changes",
    "editable_fields",
    "footer_hint",
    "handle_overlay_button",
    "render_manage_menu",
    "render_overlay",
    "save_confirm",
    "stop_confirm",
]
