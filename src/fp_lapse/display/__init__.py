"""Display abstraction layer.

Two adapters satisfy the `Display` Protocol; both are imported lazily so the
package can be imported on any machine without pulling adapter-specific
deps (numpy for the framebuffer, Tk for the mock).

    from fp_lapse.display.framebuffer import Framebuffer   # Pi only
    from fp_lapse.display.mock import TkDisplay            # Mac dev
"""

from .iface import HEIGHT, WIDTH, Display, new_canvas

__all__ = [
    "Display",
    "HEIGHT",
    "WIDTH",
    "new_canvas",
]
