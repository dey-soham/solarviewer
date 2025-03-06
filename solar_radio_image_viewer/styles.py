DARK_PALETTE = {
    "window": "#2A2A2E",
    "base": "#1E1E22",
    "text": "#FFFFFF",
    "highlight": "#3871DE",
    "button": "#404040",
    "border": "#505050",
    "disabled": "#606060",
}

STYLESHEET = f"""
    QWidget {{
        font-family: 'Segoe UI', Arial, sans-serif;
        font-size: 11pt;
    }}
    
    QGroupBox {{
        border: 1px solid {DARK_PALETTE['border']};
        border-radius: 4px;
        margin-top: 16px;
        padding-top: 8px;
    }}
    
    QGroupBox::title {{
        subcontrol-origin: margin;
        left: 12px;
        color: {DARK_PALETTE['text']};
        font-weight: bold;
        font-size: 12pt;
    }}
    
    QPushButton {{
        background-color: {DARK_PALETTE['button']};
        color: {DARK_PALETTE['text']};
        border: 1px solid {DARK_PALETTE['border']};
        border-radius: 3px;
        padding: 5px 12px;
        min-width: 80px;
        min-height: 28px;
        font-size: 11pt;
    }}
    
    QPushButton:hover {{
        background-color: #484848;
    }}

    QPushButton:pressed {{
        background-color: #303030;
    }}
    
    QPushButton:disabled {{
        color: {DARK_PALETTE['disabled']};
    }}

    QPushButton#IconOnlyButton {{
        min-width: 30px;
        max-width: 30px;
        /*min-height: 32px;*/
        max-height: 30px;
        /*padding: 2px;*/
    }}

    QPushButton#IconOnlyNBGButton {{
        background-color: transparent;  /* no background */
        border: none;                  /* no border */
        padding: 12px;                  /* no extra space */
        margin: 0px;                   /* optional: remove outer margins */
        min-width: 0px;                  /* override any global min-width */
        min-height: 0px;                 /* override any global min-height */
    }}

    QPushButton#IconOnlyNBGButton:hover {{
        background-color: #484848;  
    }}

    QPushButton#IconOnlyNBGButton:pressed {{
        background-color: #303030;
    }}


    
    QLineEdit, QComboBox, QSpinBox {{
        background-color: {DARK_PALETTE['base']};
        color: {DARK_PALETTE['text']};
        border: 1px solid {DARK_PALETTE['border']};
        border-radius: 3px;
        padding: 6px;
        min-height: 28px;
        font-size: 11pt;
    }}
    
    QTabWidget::pane {{
        border: 1px solid {DARK_PALETTE['border']};
        border-radius: 4px;
    }}
    
    QTabBar::tab {{
        background: {DARK_PALETTE['button']};
        color: {DARK_PALETTE['text']};
        padding: 10px 20px;
        border-top-left-radius: 4px;
        border-top-right-radius: 4px;
        font-size: 12pt;
    }}
    
    QTabBar::tab:selected {{
        background: {DARK_PALETTE['highlight']};
    }}
    
    QTableWidget {{
        font-size: 11pt;
    }}
    
    QTableWidget QHeaderView::section {{
        font-size: 12pt;
        font-weight: bold;
        padding: 6px;
    }}
    
    QLabel {{
        font-size: 11pt;
    }}
    
    QCheckBox {{
        font-size: 11pt;
        min-height: 24px;
    }}
    
    QRadioButton {{
        font-size: 11pt;
        min-height: 24px;
    }}
    
    QSlider {{
        min-height: 28px;
    }}
"""
