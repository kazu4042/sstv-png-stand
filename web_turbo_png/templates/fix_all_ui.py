import os
import re

template_dir = r"c:\Users\kosei\OneDrive\Desktop\sstv-aggregator\web_png\templates"
files = ["index.html", "result.html", "analytics.html", "heatmap.html", "ranking.html"]

sidebar_css = """
        /* 📱 統一サイドバー */
        .sidebar { 
            background: #ffffff; 
            border-right: 1px solid rgba(0,0,0,0.05);
            z-index: 50;
            display: flex; flex-direction: column;
            width: 280px;
            transition: width 0.3s cubic-bezier(0.4, 0, 0.2, 1), transform 0.3s cubic-bezier(0.4, 0, 0.2, 1);
            overflow-x: hidden;
            white-space: nowrap;
        }
        .sidebar.collapsed { width: 0; padding-left: 0; padding-right: 0; }
        @media (max-width: 768px) {
            .sidebar { position: fixed; height: 100%; transform: translateX(0); }
            .sidebar.collapsed { transform: translateX(-100%); width: 280px; }
        }
"""

top_nav_html = """
        <header class="top-nav h-16 px-4 flex items-center shrink-0 bg-white border-b border-gray-100 shadow-sm" style="z-index: 30;">
            <button class="p-2 rounded-full hover:bg-gray-100 text-ink/70 transition-colors" id="toggle-btn" style="background:transparent; border:none; cursor:pointer;">
                <i data-lucide="menu" class="w-6 h-6"></i>
            </button>
            <span class="ml-4 font-bold text-ink tracking-widest" style="font-size: 1.1rem;">SSTV Aggregator</span>
        </header>
"""

for fname in files:
    path = os.path.join(template_dir, fname)
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()

    # 1. Update <aside class="..."> to <aside class="sidebar collapsed" id="sidebar">
    content = re.sub(r'<aside class="sidebar[^"]*" id="sidebar">', '<aside class="sidebar collapsed" id="sidebar">', content)
    
    # 2. Add or update the top-nav
    if fname == "index.html":
        content = re.sub(r'<header class="top-nav[^>]*>[\s\S]*?</header>', top_nav_html, content)
    elif fname == "result.html":
        content = re.sub(r'<header class="top-nav">[\s\S]*?</header>', top_nav_html, content)
    elif fname == "analytics.html":
        content = re.sub(r'<button class="toggle-btn"[^>]*>[\s\S]*?</button>', '', content)
        if '<header class="top-nav' not in content:
            content = content.replace('<main class="main-wrapper">', '<main class="main-wrapper">\n' + top_nav_html)
    elif fname == "heatmap.html":
        content = re.sub(r'<button class="toggle-btn"[^>]*>[\s\S]*?</button>', '', content)
        if '<header class="top-nav' not in content:
            # heatmap uses <div class="container"> as main wrapper sometimes
            if '<div class="container">' in content:
                content = content.replace('<div class="container">', '<div class="container" style="flex-direction:column;">\n' + top_nav_html)
    elif fname == "ranking.html":
        content = re.sub(r'<button class="toggle-btn"[^>]*>[\s\S]*?</button>', '', content)
        if '<header class="top-nav' not in content:
            if '<main class="main-wrapper">' in content:
                content = content.replace('<main class="main-wrapper">', '<main class="main-wrapper">\n' + top_nav_html)
            elif '<div class="container">' in content:
                content = content.replace('<div class="container">', '<div class="container" style="flex-direction:column;">\n' + top_nav_html)

    # remove old top-nav classes in styles just in case
    content = re.sub(r"\.top-nav\s*\{[^}]+\}", "", content)

    # 3. Fix CSS for sidebar
    content = re.sub(r"\.sidebar\s*\{[^}]+\}", "", content)
    content = re.sub(r"\.sidebar\.collapsed\s*\{[^}]+\}", "", content)
    content = re.sub(r"@media\s*\([^)]+\)\s*\{\s*\.sidebar[^{]*\{[^}]+\}\s*\.sidebar\.collapsed\s*\{[^}]+\}\s*\}", "", content)
    
    if "/* 📱 統一サイドバー */" not in content:
        content = content.replace("<style>", "<style>\n" + sidebar_css)
        
    # 4. Fix JS toggle logic
    # Clean up old click listeners
    content = re.sub(r"document\.getElementById\('toggle-btn'\)\.addEventListener\('click', function\(\) \{[\s\S]*?\}\);", "", content)
    content = re.sub(r"document\.getElementById\('close-sidebar'\)\.addEventListener\('click', function\(\) \{[\s\S]*?\}\);", "", content)
    content = re.sub(r"toggleBtn\.addEventListener\('click', function\(\) \{[\s\S]*?\}\);", "", content)
    content = re.sub(r"closeBtn\.addEventListener\('click', function\(\) \{[\s\S]*?\}\);", "", content)
    
    js_new = """
        const _sidebar = document.getElementById('sidebar');
        const _toggleBtn = document.getElementById('toggle-btn');
        const _closeBtn = document.getElementById('close-sidebar');
        if(_toggleBtn && _sidebar) {
            _toggleBtn.addEventListener('click', function() {
                _sidebar.classList.toggle('collapsed');
            });
        }
        if(_closeBtn && _sidebar) {
            _closeBtn.addEventListener('click', function() {
                _sidebar.classList.add('collapsed');
            });
        }
    """
    
    # Find a good place to inject the JS
    if "const _sidebar = document.getElementById('sidebar');" not in content:
        content = content.replace("</body>", "<script>\n" + js_new + "\n</script>\n</body>")
            
    # 5. Fix analytics image squishing
    if fname == "analytics.html":
        content = content.replace("width: 640px;", "width: 100%; max-width: 640px;")
        
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)

print("All UI elements fixed successfully!")
