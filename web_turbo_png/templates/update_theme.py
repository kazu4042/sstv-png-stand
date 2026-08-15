import os

template_dir = r"c:\Users\kosei\OneDrive\Desktop\sstv-aggregator\web_png\templates"
files = ["result.html", "ranking.html", "analytics.html", "heatmap.html"]

tailwind_head = """
    <!-- Fonts -->
    <link rel="preconnect" href="https://fonts.googleapis.com">
    <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
    <link href="https://fonts.googleapis.com/css2?family=Zen+Kaku+Gothic+New:wght@400;500;700&display=swap" rel="stylesheet">
    
    <!-- Tailwind CSS -->
    <script src="https://cdn.tailwindcss.com"></script>
    
    <!-- Tailwind Config -->
    <script>
        tailwind.config = {
            theme: {
                extend: {
                    colors: {
                        forest: '#008d54',
                        forestLight: '#e6f3ec',
                        earth: '#8b7d6b',
                        ink: '#231815',
                        paper: '#f9f9f9'
                    },
                    fontFamily: {
                        sans: ['"Zen Kaku Gothic New"', 'sans-serif'],
                    }
                }
            }
        }
    </script>
"""

for fname in files:
    path = os.path.join(template_dir, fname)
    with open(path, "r", encoding="utf-8") as f:
        content = f.read()
        
    if "cdn.tailwindcss.com" not in content:
        content = content.replace("</head>", tailwind_head + "</head>")
        
    # Basic font updates in custom style blocks
    content = content.replace("font-family: -apple-system, BlinkMacSystemFont, \"Segoe UI\", Roboto, Helvetica, Arial, sans-serif;", "font-family: \"Zen Kaku Gothic New\", sans-serif;")
    content = content.replace("font-family: -apple-system, BlinkMacSystemFont, \"Segoe UI\", Roboto, sans-serif;", "font-family: \"Zen Kaku Gothic New\", sans-serif;")
    content = content.replace("font-family:'Inter', -apple-system, BlinkMacSystemFont, \"Segoe UI\", Roboto, sans-serif;", "font-family: \"Zen Kaku Gothic New\", sans-serif;")
    content = content.replace("h1, h2, h3 { font-family: 'Inter', -apple-system, sans-serif; }", "h1, h2, h3 { font-family: \"Zen Kaku Gothic New\", sans-serif; }")
    
    # Colors updates
    content = content.replace("background: #f0f4f9; color: #1f1f1f;", "background: #f9f9f9; color: #231815;")
    content = content.replace("background:#f0f4f9; color:#1f1f1f;", "background:#f9f9f9; color:#231815;")
    content = content.replace("background-color: #0f172a;", "background-color: #f9f9f9;")
    content = content.replace("color: #f1f5f9;", "color: #231815;")
    content = content.replace("background: #f8fafc;", "background: #ffffff;")
    
    content = content.replace("background: #1e1e2d;", "background: #ffffff; border-right: 1px solid #e6f3ec;")
    content = content.replace("color: #f8fafc;", "color: #231815;")
    content = content.replace("background: #0f172a; border-right: 1px solid #1e293b;", "background: #ffffff; border-right: 1px solid #e6f3ec;")
    content = content.replace("background: #1e293b; color: #38bdf8;", "background: #e6f3ec; color: #008d54;")
    content = content.replace("background: #38bdf8; color: #0f172a;", "background: #008d54; color: #ffffff;")
    content = content.replace("color: #c4c7c5;", "color: #231815;")
    content = content.replace("color: #a8c7fa;", "color: #8b7d6b;")
    
    content = content.replace("background: #0b57d0;", "background: #008d54;")
    content = content.replace("color: #0b57d0;", "color: #008d54;")
    content = content.replace("background: linear-gradient(90deg, #0b57d0, #38bdf8);", "background: #008d54;")
    content = content.replace("background: linear-gradient(135deg, #38bdf8, #0ea5e9);", "background: #008d54;")
    content = content.replace("background: linear-gradient(135deg, #0ea5e9, #0284c7);", "background: #008d54;")
    content = content.replace("background: #334155;", "background: #231815;")
    content = content.replace("color: #38bdf8;", "color: #008d54;")
    content = content.replace("color:#38bdf8;", "color:#008d54;")
    content = content.replace("border-color: #38bdf8;", "border-color: #008d54;")
    content = content.replace("border-left: 3px solid #38bdf8;", "border-left: 3px solid #008d54;")
    content = content.replace("border-left-color: #00ffff;", "border-left-color: #008d54;")
    
    content = content.replace("background: #1e293b;", "background: #ffffff;")
    content = content.replace("border: 1px solid #334155;", "border: 1px solid #e6f3ec;")
    content = content.replace("background: #0f172a;", "background: #f9f9f9;")
    content = content.replace("color: #f8fafc;", "color: #231815;")
    content = content.replace("color:#f8fafc;", "color:#231815;")
    content = content.replace("color: #64748b;", "color: #8b7d6b;")
    content = content.replace("color:#64748b;", "color:#8b7d6b;")
    
    content = content.replace("background:#0f172a; color:#f1f5f9;", "background:#f9f9f9; color:#231815;")
    
    # Extra fix for ranking.html empty states / tables
    content = content.replace("background:#f0f4f9; color:#475569;", "background:#e6f3ec; color:#8b7d6b;")
    
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
        
print("Updates complete!")
