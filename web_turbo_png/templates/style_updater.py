import os
import re

template_dir = r"c:\Users\kosei\OneDrive\Desktop\sstv-aggregator\web_png\templates"

replacements = {
    "🌍": '<i data-lucide="globe" class="icon-inline"></i>',
    "🏠": '<i data-lucide="home" class="icon-inline"></i>',
    "📊": '<i data-lucide="layout-dashboard" class="icon-inline"></i>',
    "🔬": '<i data-lucide="activity" class="icon-inline"></i>',
    "🏆": '<i data-lucide="award" class="icon-inline"></i>',
    "📡": '<i data-lucide="radio" class="icon-inline"></i>',
    "📁": '<i data-lucide="folder" class="icon-inline"></i>',
    "▶ ": '<i data-lucide="play" class="icon-inline"></i> ',
    "⏸ ": '<i data-lucide="pause" class="icon-inline"></i> ',
    "🚀": '<i data-lucide="rocket" class="icon-inline"></i>',
    "⏳": '<i data-lucide="hourglass" class="icon-inline"></i>',
    "🎉": '<i data-lucide="party-popper" class="icon-inline"></i>',
    "⚠️": '<i data-lucide="alert-triangle" class="icon-inline"></i>',
    "💡": '<i data-lucide="lightbulb" class="icon-inline"></i>',
    "📷": '<i data-lucide="camera" class="icon-inline"></i>',
    "✨": '<i data-lucide="sparkles" class="icon-inline"></i>',
    "❌": '<i data-lucide="x-circle" class="icon-inline"></i>',
    "🌟": '<i data-lucide="star" class="icon-inline"></i>',
    "🪂": '<i data-lucide="plane" class="icon-inline"></i>',
    "📍": '<i data-lucide="map-pin" class="icon-inline"></i>',
    "💾": '<i data-lucide="save" class="icon-inline"></i>',
    "🔄": '<i data-lucide="refresh-cw" class="icon-inline"></i>',
    "🔍": '<i data-lucide="search" class="icon-inline"></i>',
    "◀ ": '<i data-lucide="chevron-left" class="icon-inline"></i> ',
    "🎯": '<i data-lucide="crosshair" class="icon-inline"></i>',
    "🔎": '<i data-lucide="zoom-in" class="icon-inline"></i>',
    "📄": '<i data-lucide="file-audio" class="icon-inline"></i>'
}

css_addition = """
        /* 共通アイコンスタイル */
        .icon-inline {
            width: 1.1em; height: 1.1em;
            vertical-align: -0.15em;
            display: inline-block;
        }
        
        /* サイドバー洗練 */
        .sidebar { background: #0f172a; border-right: 1px solid #1e293b; }
        .sidebar-header h3 { font-size: 0.85rem; text-transform: uppercase; color: #64748b; font-weight: 700; letter-spacing: 0.05em; }
        .session-item { border-radius: 8px; margin-bottom: 4px; padding: 10px 16px; display: flex; align-items: center; gap: 12px; font-weight: 500; font-size: 0.95rem; }
        .session-item:hover { background: #1e293b; color: #38bdf8; }
        .session-item.active { background: #38bdf8; color: #0f172a; font-weight: 600; }
        .session-item.active .icon-inline { color: #0f172a; }
        
        .top-nav { background: rgba(255, 255, 255, 0.8); backdrop-filter: blur(8px); border-bottom: 1px solid #e2e8f0; }
        .main-wrapper { background: #f8fafc; }
        
        /* Typography */
        h1, h2, h3 { font-family: 'Inter', -apple-system, sans-serif; }
"""

lucide_script = '<script src="https://unpkg.com/lucide@latest"></script>\n</head>'
lucide_init = '<script>lucide.createIcons();</script>\n</body>'

for filename in os.listdir(template_dir):
    if filename.endswith(".html"):
        filepath = os.path.join(template_dir, filename)
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()

        # Add Lucide script
        if "lucide@latest" not in content:
            content = content.replace("</head>", lucide_script)
            content = content.replace("</body>", lucide_init)
            
        # Add CSS
        if ".icon-inline" not in content:
            content = content.replace("</style>", css_addition + "</style>")

        # Replace Emojis
        for emoji, html_tag in replacements.items():
            content = content.replace(emoji, html_tag)
            
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)
            
print("Done styling update.")
