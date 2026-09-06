"""Offline resize regression exercised by source and frozen GUI smoke checks."""

import customtkinter as ctk


def check_layout(app):
    def settle(delay=250):
        # Window-manager and DPI changes settle asynchronously, especially on Windows.
        done = ctk.BooleanVar(master=app, value=False)
        app.after(delay, lambda: done.set(True))
        app.wait_variable(done)
        app.update()

    def contained(widget, viewport, label):
        assert widget.winfo_ismapped(), f"{label} is hidden"
        x = widget.winfo_rootx() - viewport.winfo_rootx()
        y = widget.winfo_rooty() - viewport.winfo_rooty()
        assert x >= -1 and y >= -1, f"{label} starts outside viewport: {x}, {y}"
        assert x + widget.winfo_width() <= viewport.winfo_width() + 1, f"{label} clipped horizontally"
        assert y + widget.winfo_height() <= viewport.winfo_height() + 1, f"{label} clipped vertically: y={y}, height={widget.winfo_height()}, viewport={viewport.winfo_height()}"

    app.show_main()
    app.header.configure(text="Example Hall · Example Room")
    app.balance_label.configure(text="Balance: SGD 5.00")
    app.footer.configure(text="Updated 12:00:00")
    app.power_btn.configure(text="OFF")
    app.swing_hint.configure(text="Swing is not supported by this unit")
    app._render_usage([{"starttime": "2026-01-01T12:00", "duration": 10, "amount": "0.07"}] * 20)
    app._render_topups([{"created_on": "2026-01-01", "amount": "5.00", "type": "Top-up", "txn_id": "example"}] * 10)
    app.deiconify()
    settle()
    try:
        for scale in (1.0, 1.25):
            ctk.set_widget_scaling(scale)
            ctk.set_window_scaling(scale)
            # CustomTkinter holds min/max dimensions for one second after scaling.
            settle(1100)
            for height in (420, 540, 660, 900):
                app.geometry(f"440x{height}")
                settle()
                contained(app.logout_btn, app, "Log out")
                contained(app.footer, app, "Status")
                for tab, scroll, first, last in (
                    ("Control", app.control_scroll, app.balance_label, app.swing_hint),
                    ("History", app.history_scroll, app.usage_summary, app.topup_box.winfo_children()[-1]),
                ):
                    app.tabs.set(tab)
                    settle(150)
                    canvas = scroll._parent_canvas
                    contained(canvas, app, f"{tab} viewport")
                    canvas.yview_moveto(0)
                    app.update()
                    contained(first, canvas, f"{tab} first content")
                    canvas.yview_moveto(1)
                    app.update()
                    contained(last, canvas, f"{tab} last content")
                app.show_login()
                app._after_verify({"data": {"ad_status": True}})
                app.update()
                canvas = app.login_scroll._parent_canvas
                canvas.yview_moveto(1)
                app.update()
                contained(app.sso_finish_btn, canvas, "Finish sign-in")
                contained(app.back_btn, canvas, "Back")
                app.show_main()
    finally:
        ctk.set_widget_scaling(1.0)
        ctk.set_window_scaling(1.0)
