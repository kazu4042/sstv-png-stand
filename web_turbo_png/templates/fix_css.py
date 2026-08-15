import os

template_dir = r"c:\Users\kosei\OneDrive\Desktop\sstv-aggregator\web_png\templates"

light_theme = """        .top-nav { background: rgba(255, 255, 255, 0.8); backdrop-filter: blur(8px); border-bottom: 1px solid #e2e8f0; }
        .main-wrapper { background: #f8fafc; }"""

dark_theme = """        .top-nav { background: rgba(30, 41, 59, 0.8); backdrop-filter: blur(8px); border-bottom: 1px solid #334155; }
        .main-wrapper { background: #0f172a; }"""

for filename in ["index.html", "analytics.html"]:
    filepath = os.path.join(template_dir, filename)
    with open(filepath, "r", encoding="utf-8") as f:
        content = f.read()
    
    content = content.replace(light_theme, dark_theme)
    
    with open(filepath, "w", encoding="utf-8") as f:
        f.write(content)

# Fix heatmap.html controls overlap
heatmap_path = os.path.join(template_dir, "heatmap.html")
with open(heatmap_path, "r", encoding="utf-8") as f:
    heatmap_content = f.read()

bad_controls = """        .controls {
            display: flex;
            gap: 15px;
            flex-wrap: wrap;
            align-items: center;
            position: absolute;
            left: 30px;
            top: 70px;
        }"""

good_controls = """        .controls {
            display: flex;
            gap: 15px;
            flex-wrap: wrap;
            align-items: center;
            flex: 1;
            justify-content: flex-end;
        }"""

heatmap_content = heatmap_content.replace(bad_controls, good_controls)

with open(heatmap_path, "w", encoding="utf-8") as f:
    f.write(heatmap_content)

print("CSS Fixed!")
