"""
Pop-out window utility — opens any panel in a separate resizable window.
"""
import tkinter as tk


def add_popout_button(panel_frame, panel_title, build_fn):
    """
    Add a small pop-out button to the top-right corner of a panel.

    Args:
        panel_frame: the panel's outer tk.Frame
        panel_title: window title when popped out
        build_fn: function that builds the panel content (takes a parent frame)
    """
    btn = tk.Button(
        panel_frame,
        text="⧉",  # pop-out icon
        font=("Arial", 12),
        bg="#667eea",
        fg="white",
        relief=tk.FLAT,
        cursor="hand2",
        padx=6,
        pady=2,
        command=lambda: _open_popout(panel_title, build_fn),
    )
    btn.place(relx=1.0, x=-40, y=5, anchor="ne")

    # Tooltip on hover
    def _enter(e):
        btn.config(bg="#5a6fd6")
    def _leave(e):
        btn.config(bg="#667eea")
    btn.bind("<Enter>", _enter)
    btn.bind("<Leave>", _leave)


def _open_popout(title, build_fn):
    """Open a new top-level window with the panel content."""
    # WHY (Phase 76 Fix 50): Toplevel without a master is orphaned from
    #      the main app. Closing main app leaves the popout alive.
    # CHANGED: April 2026 — Phase 76 Fix 50 — parent reference
    import state as _state
    _parent = getattr(_state, 'window', None)
    win = tk.Toplevel(_parent)
    win.title(f"Trade Bot — {title}")
    win.geometry("1200x800")
    win.minsize(800, 600)

    # Build panel content inside the new window
    try:
        panel = build_fn(win)
        panel.pack(fill="both", expand=True)
    except Exception as e:
        tk.Label(win, text=f"Error loading panel:\n{e}",
                 font=("Arial", 12), fg="red", bg="white",
                 wraplength=600).pack(expand=True)

    # Focus the new window
    win.lift()
    win.focus_force()
