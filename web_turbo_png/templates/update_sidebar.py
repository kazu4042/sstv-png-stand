import os

template_dir = r"c:\Users\kosei\OneDrive\Desktop\sstv-aggregator\web_png\templates"

old_link = '<a href="/" class="session-item" style="text-decoration:none;"><i data-lucide="home" class="icon-inline"></i> ホーム</a>'
new_link = '<a href="/" class="session-item" style="text-decoration:none;"><i data-lucide="home" class="icon-inline"></i> ホーム</a>\n        <a href="/app" class="session-item" style="text-decoration:none;"><i data-lucide="rocket" class="icon-inline"></i> アップロード</a>'

old_link_active = '<a href="/" class="session-item active" style="text-decoration:none;"><i data-lucide="home" class="icon-inline"></i> ホーム</a>'
new_link_active = '<a href="/" class="session-item" style="text-decoration:none;"><i data-lucide="home" class="icon-inline"></i> ホーム</a>\n        <a href="/app" class="session-item active" style="text-decoration:none;"><i data-lucide="rocket" class="icon-inline"></i> アップロード</a>'

for filename in os.listdir(template_dir):
    if filename.endswith(".html") and filename != "landing.html":
        filepath = os.path.join(template_dir, filename)
        with open(filepath, "r", encoding="utf-8") as f:
            content = f.read()
            
        if filename == "index.html":
            content = content.replace(old_link_active, new_link_active)
        else:
            content = content.replace(old_link, new_link)
            
        with open(filepath, "w", encoding="utf-8") as f:
            f.write(content)

print("Sidebar links updated!")
