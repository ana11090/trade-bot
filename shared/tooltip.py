"""
Tooltip utility — hover over any widget to see a scrollable popup.
Long tooltips get a scrollbar so all content is readable.
"""
import tkinter as tk


class ToolTip:
    """Tooltip that appears when hovering over a widget.

    Long content is scrollable with the mousewheel.
    """

    def __init__(self, widget, text, delay=400, wraplength=400):
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

        screen_w = self.widget.winfo_screenwidth()
        screen_h = self.widget.winfo_screenheight()

        # Max tooltip height = 60% of screen
        max_height = int(screen_h * 0.6)

        # Keep tooltip on screen horizontally
        if x + self.wraplength + 60 > screen_w:
            x = screen_w - self.wraplength - 80

        self._tipwindow = tw = tk.Toplevel(self.widget)
        tw.wm_overrideredirect(True)
        tw.attributes("-topmost", True)

        outer = tk.Frame(tw, bg="#333333", bd=1, relief=tk.SOLID)
        outer.pack(fill="both", expand=True)

        # Create a Text widget for scrollable content
        text_widget = tk.Text(
            outer,
            font=("Consolas", 9),
            bg="#333333", fg="white",
            wrap=tk.WORD,
            width=int(self.wraplength / 7),  # approximate char width
            padx=10, pady=8,
            relief=tk.FLAT,
            highlightthickness=0,
            cursor="arrow",
            borderwidth=0,
        )
        text_widget.insert("1.0", self.text)
        text_widget.config(state=tk.DISABLED)  # read-only

        # Calculate how tall the content would be
        text_widget.update_idletasks()
        # Count lines (wrapped)
        line_count = int(text_widget.index('end-1c').split('.')[0])
        line_height = 16  # approximate pixels per line
        content_height = line_count * line_height + 20

        if content_height > max_height:
            # Content overflows — show with scrollbar
            display_height = max_height

            scrollbar = tk.Scrollbar(outer, command=text_widget.yview)
            text_widget.configure(yscrollcommand=scrollbar.set)

            text_widget.pack(side=tk.LEFT, fill="both", expand=True)
            scrollbar.pack(side=tk.RIGHT, fill="y")

            # Set window size
            tw.wm_geometry(f"{self.wraplength + 40}x{display_height}+{x}+{y}")

            # If tooltip goes below screen, move it up
            if y + display_height > screen_h:
                y = max(10, screen_h - display_height - 20)
                tw.wm_geometry(f"{self.wraplength + 40}x{display_height}+{x}+{y}")

            # Mousewheel scrolls the tooltip content
            def _on_scroll(event):
                text_widget.yview_scroll(int(-1 * (event.delta / 120)), "units")
                return "break"

            # Bind mousewheel to all tooltip widgets
            tw.bind("<MouseWheel>", _on_scroll)
            outer.bind("<MouseWheel>", _on_scroll)
            text_widget.bind("<MouseWheel>", _on_scroll)
            scrollbar.bind("<MouseWheel>", _on_scroll)
            # Linux scroll
            tw.bind("<Button-4>", lambda e: (text_widget.yview_scroll(-3, "units"), "break"))
            tw.bind("<Button-5>", lambda e: (text_widget.yview_scroll(3, "units"), "break"))
        else:
            # Content fits — simple display, no scrollbar
            text_widget.pack(fill="both", expand=True)

            # Fit height to content
            actual_height = min(content_height + 10, max_height)
            tw.wm_geometry(f"{self.wraplength + 30}x{actual_height}+{x}+{y}")

            # If tooltip goes below screen, show above the widget
            if y + actual_height > screen_h:
                y = max(10, self.widget.winfo_rooty() - actual_height - 5)
                tw.wm_geometry(f"{self.wraplength + 30}x{actual_height}+{x}+{y}")

            # Forward mousewheel to parent canvas (original behavior for short tips)
            def _forward_scroll(event):
                self._hide()
                parent = self.widget
                while parent:
                    if isinstance(parent, tk.Canvas):
                        parent.yview_scroll(int(-1 * (event.delta / 120)), "units")
                        return
                    parent = parent.master

            tw.bind("<MouseWheel>", _forward_scroll)
            outer.bind("<MouseWheel>", _forward_scroll)
            text_widget.bind("<MouseWheel>", _forward_scroll)

        # Hide when mouse leaves the tooltip
        def _on_leave_tip(event):
            # Check if mouse actually left the tooltip window
            try:
                mx = tw.winfo_pointerx()
                my = tw.winfo_pointery()
                tx = tw.winfo_rootx()
                ty = tw.winfo_rooty()
                tw_w = tw.winfo_width()
                tw_h = tw.winfo_height()
                # Small margin to prevent flicker
                margin = 5
                if (mx < tx - margin or mx > tx + tw_w + margin or
                    my < ty - margin or my > ty + tw_h + margin):
                    self._hide()
            except Exception:
                self._hide()

        tw.bind("<Leave>", _on_leave_tip)

    def _hide(self, event=None):
        self._cancel()
        if self._tipwindow:
            try:
                self._tipwindow.destroy()
            except Exception:
                pass
            self._tipwindow = None


def add_tooltip(widget, text, **kwargs):
    """Add a hover tooltip to any widget. Returns the ToolTip instance."""
    return ToolTip(widget, text, **kwargs)
