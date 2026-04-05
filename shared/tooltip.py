"""
Tooltip utility — hover over any widget to see an explanation popup.
Tooltips never capture mousewheel — scrolling always works.
"""
import tkinter as tk


class ToolTip:
    """Tooltip that appears when hovering over a widget. Never blocks scrolling."""

    def __init__(self, widget, text, delay=400, wraplength=350):
        self.widget = widget
        self.text = text
        self.delay = delay
        self.wraplength = wraplength
        self._tipwindow = None
        self._id = None

        widget.bind("<Enter>", self._schedule, add="+")
        widget.bind("<Leave>", self._hide, add="+")
        widget.bind("<ButtonPress>", self._hide, add="+")

    def _schedule(self, event=None):
        self._cancel()
        self._id = self.widget.after(self.delay, self._show)

    def _cancel(self):
        if self._id:
            self.widget.after_cancel(self._id)
            self._id = None

    def _show(self):
        if self._tipwindow:
            return
        x = self.widget.winfo_rootx() + 20
        y = self.widget.winfo_rooty() + self.widget.winfo_height() + 5

        # Keep tooltip on screen
        screen_w = self.widget.winfo_screenwidth()
        screen_h = self.widget.winfo_screenheight()
        if x + self.wraplength + 40 > screen_w:
            x = screen_w - self.wraplength - 60
        if y + 200 > screen_h:
            y = self.widget.winfo_rooty() - 100  # show above instead

        self._tipwindow = tw = tk.Toplevel(self.widget)
        tw.wm_overrideredirect(True)
        tw.wm_geometry(f"+{x}+{y}")

        # Make tooltip non-interactive — clicks and scrolls pass through
        tw.attributes("-topmost", True)

        frame = tk.Frame(tw, bg="#333333", padx=10, pady=8, bd=1, relief=tk.SOLID)
        frame.pack()

        label = tk.Label(frame, text=self.text, font=("Arial", 9), bg="#333333", fg="white",
                 justify=tk.LEFT, wraplength=self.wraplength)
        label.pack()

        # Forward mousewheel from tooltip to the parent's scroll canvas
        def _forward_scroll(event):
            self._hide()
            # Find the nearest scrollable canvas in the widget hierarchy
            parent = self.widget
            while parent:
                if isinstance(parent, tk.Canvas):
                    parent.yview_scroll(int(-1 * (event.delta / 120)), "units")
                    return
                parent = parent.master

        tw.bind("<MouseWheel>", _forward_scroll)
        frame.bind("<MouseWheel>", _forward_scroll)
        label.bind("<MouseWheel>", _forward_scroll)

        # Also hide tooltip on any scroll
        tw.bind("<Enter>", lambda e: None)  # don't re-trigger
        tw.bind("<Leave>", lambda e: self._hide())

    def _hide(self, event=None):
        self._cancel()
        if self._tipwindow:
            self._tipwindow.destroy()
            self._tipwindow = None


def add_tooltip(widget, text, **kwargs):
    """Add a hover tooltip to any widget. Returns the ToolTip instance."""
    return ToolTip(widget, text, **kwargs)
