import os
import re

template_dir = r"c:\Users\kosei\OneDrive\Desktop\sstv-aggregator\web_png\templates"
files = ["index.html", "result.html", "analytics.html", "heatmap.html", "ranking.html"]

sidebar_script = "\n<script>if(window.innerWidth <= 768) document.getElementById('sidebar').classList.add('collapsed');</script>\n"

for fname in files:
    path = os.path.join(template_dir, fname)
    if not os.path.exists(path): continue
    
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()

    # 1. Sidebar issue: remove 'collapsed' from HTML so it stays open on desktop.
    content = content.replace('<aside class="sidebar collapsed"', '<aside class="sidebar"')
    
    # Insert the mobile-collapse script right after </aside> if not present
    if "if(window.innerWidth <= 768)" not in content:
        content = content.replace("</aside>", "</aside>" + sidebar_script)

    # 2. Analytics specific fixes
    if fname == "analytics.html":
        # Fix height stretching
        content = content.replace("height: 480px;", "height: auto;")
        
        # Fix badge-id visibility
        new_badge_css = """
        .badge-id { 
            background: #1f2937 !important; 
            color: #4ade80 !important; 
            padding: 6px 12px; 
            border-radius: 8px; 
            font-size: 1rem; 
            font-family: monospace; 
            border: 2px solid #374151; 
            font-weight: bold;
        }
        """
        content = re.sub(r"\.badge-id\s*\{[^}]+\}", new_badge_css.strip(), content)

        # Fix canvas resize logic in JS
        old_canvas_js = r"canvas\.width\s*=\s*img\.clientWidth\s*\|\|\s*640;\s*canvas\.height\s*=\s*img\.clientHeight\s*\|\|\s*480;"
        new_canvas_js = """
            canvas.width = img.clientWidth || 640;
            canvas.height = img.clientHeight || 480;
            
            // Resize canvas when image fully loads to prevent misalignment
            img.addEventListener('load', function() {
                canvas.width = img.clientWidth;
                canvas.height = img.clientHeight;
                displayToNativeScaleX = canvas.width / (img.naturalWidth || parseInt("{{ width }}") || 256);
                displayToNativeScaleY = canvas.height / (img.naturalHeight || parseInt("{{ height }}") || 256);
            });
        """
        
        # Replace only if not already added
        if "img.addEventListener('load'" not in content:
            content = re.sub(old_canvas_js, new_canvas_js, content)
            
        # Also need to make displayToNativeScaleX/Y mutable
        content = content.replace("const displayToNativeScaleX", "let displayToNativeScaleX")
        content = content.replace("const displayToNativeScaleY", "let displayToNativeScaleY")

    # 3. Result specific fixes
    if fname == "result.html":
        # Make image ID extremely visible
        content = re.sub(r"\.active-session-header\s*\{[^}]+\}", 
            ".active-session-header { background: #1e293b; padding: 15px 24px; border-radius: 12px; border: 1px solid #334155; margin: 20px 0; display: flex; align-items: center; justify-content: space-between; }", content)
        content = re.sub(r"\.session-label\s*\{[^}]+\}", 
            ".session-label { font-weight: bold; color: #94a3b8; font-size: 1.05em; }", content)
        content = re.sub(r"\.display-id\s*\{[^}]+\}", 
            ".display-id { font-size: 1.8rem; font-weight: bold; color: #4ade80; font-family: monospace; letter-spacing: 2px; text-shadow: 0 0 10px rgba(74, 222, 128, 0.3); }", content)

    with open(path, "w", encoding="utf-8") as f:
        f.write(content)

print("Bugs fixed successfully!")
