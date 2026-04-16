import subprocess
prompts = [
    '请输出一份中文 Markdown 需求设计，主题：轻量任务中心。包含目标、方案、风险。',
    '任务中心需求设计。输出 Markdown：目标、关键假设、方案、交付物、风险。',
    '输出中文 Markdown。标题：需求设计。内容：目标、方案、风险。',
]
for idx, prompt in enumerate(prompts, start=1):
    cmd = [r'C:\Users\cxs96\AppData\Roaming\npm\claude.CMD', '-p', prompt, '--output-format', 'text', '--effort', 'high']
    result = subprocess.run(cmd, cwd=r'C:\99_self\conductor\conductor-ai', capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=180)
    print('PROMPT', idx, 'code=', result.returncode)
    print(result.stdout[:1200])
    print('---')
