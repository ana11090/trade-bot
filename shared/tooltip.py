"""
Tooltip utility — hover over any widget to see an explanation popup.
"""
import tkinter as tk


class ToolTip:
    """Tooltip that appears when hovering over a widget."""

    def __init__(self, widget, text, delay=400, wraplength=350):
        self.widget = widget
        self.text = text
        self.delay = delay
        self.wraplength = wraplength
        self._tipwindow = None
        self._id = None

        widget.bind("<Enter>", self._schedule)
        widget.bind("<Leave>", self._hide)
        widget.bind("<ButtonPress>", self._hide)

    def _schedule(self, event=None):
        self._id = self.widget.after(self.delay, self._show)

    def _show(self):
        if self._tipwindow:
            return
        x = self.widget.winfo_rootx() + 20
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 5

        self._tipwindow = tw = tk.Toplevel(self.widget)
        tw.wm_overrideredirect(True)
        tw.wm_geometry(f"+{x}+{y}")

        frame = tk.Frame(tw, bg="#333333", padx=10, pady=8, bd=1, relief=tk.SOLID)
        frame.pack()

        tk.Label(frame, text=self.text, font=("Arial", 9), bg="#333333", fg="white",
                 justify=tk.LEFT, wraplength=self.wraplength).pack()

    def _hide(self, event=None):
        if self._id:
            self.widget.after_cancel(self._id)
            self._id = None
        if self._tipwindow:
            self._tipwindow.destroy()
            self._tipwindow = None


def add_tooltip(widget, text, **kwargs):
    """Add a hover tooltip to any widget. Returns the ToolTip instance."""
    return ToolTip(widget, text, **kwargs)
