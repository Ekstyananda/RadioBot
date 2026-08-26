"""
Backward compatibility — re-export dari dashboard_ui.
Semua import lama `from views.radio_ui import ControlButtons` tetap bekerja.
"""

from views.dashboard_ui import DashboardView, RadioSelect, ControlButtons, build_dashboard_embed

__all__ = ['DashboardView', 'RadioSelect', 'ControlButtons', 'build_dashboard_embed']