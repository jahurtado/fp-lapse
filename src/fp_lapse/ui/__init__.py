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
from .keyboard import (
    KeyboardAction,
    KeyboardInteraction,
    KeyboardState,
    render_keyboard,
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
    poweroff_confirm,
    render_overlay,
    save_confirm,
    stop_confirm,
    wifi_forget_confirm,
)
from .wifi_screen import (
    WifiListAction,
    WifiListInteraction,
    WifiListState,
    WifiStatusState,
    render_wifi_list,
    render_wifi_status,
)
from .shutdown_screen import render_powering_off
from .picker_datetime import (
    DateTimePickerInteraction,
    PickerAction,
    PickerMode,
    PickerState,
    render_datetime_picker,
)
from .schedule_indicator import ScheduleIndicator
from .time_setup_menu import (
    TimeSetupMenuAction,
    TimeSetupMenuInteraction,
    TimeSetupMenuState,
    render_time_setup_menu,
)

__all__ = [
    "DateTimePickerInteraction",
    "KeyboardAction",
    "KeyboardInteraction",
    "KeyboardState",
    "render_keyboard",
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
    "PickerAction",
    "PickerMode",
    "PickerState",
    "ScheduleIndicator",
    "TimeSetupMenuAction",
    "TimeSetupMenuInteraction",
    "TimeSetupMenuState",
    "UIState",
    "WifiListAction",
    "WifiListInteraction",
    "WifiListState",
    "WifiStatusState",
    "delete_confirm",
    "discard_changes",
    "editable_fields",
    "footer_hint",
    "handle_overlay_button",
    "poweroff_confirm",
    "render_datetime_picker",
    "render_manage_menu",
    "render_overlay",
    "render_powering_off",
    "render_time_setup_menu",
    "render_wifi_list",
    "render_wifi_status",
    "save_confirm",
    "stop_confirm",
    "wifi_forget_confirm",
]
