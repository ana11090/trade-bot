import tkinter as tk


def _sep(parent):
    tk.Frame(parent, bg="#eeeeee", height=1).pack(fill="x", padx=16, pady=8)


def _rule_box(parent, lines):
    """Light-grey box showing rule text — each item in lines is (text, is_bold)."""
    box = tk.Frame(parent, bg="#f5f7fa", bd=0)
    box.pack(fill="x", padx=16, pady=(0, 10))
    for text, bold in lines:
        tk.Label(box, text=text, bg="#f5f7fa",
                 fg="#1a1a2a" if bold else "#555566",
                 font=("Segoe UI", 9, "bold" if bold else "normal"),
                 justify="left", anchor="w").pack(anchor="w", padx=12,
                                                  pady=(6, 0) if bold else (1, 0))
    tk.Frame(box, height=6, bg="#f5f7fa").pack()  # bottom padding
