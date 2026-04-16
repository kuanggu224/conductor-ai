import subprocess
cmd = [r'C:\Users\cxs96\AppData\Roaming\npm\claude.CMD', '-p', '你是 Conductor 的 designer agent。\n请产出产品/设计文档，不要反问用户；信息不足时基于现有需求给出合理假设。\nWorkItem ID: workitem-001\n类型: design_overview\n描述: 梳理需求并形成总体设计：为一个轻量任务中心输出需求设计和测试设计文档。\n验收标准:\n- 输出设计要点\n- 明确下一阶段实现边界\n\n请使用中文输出结构化 Markdown 文档，包含：目标、关键假设、方案、交付物、风险。', '--output-format', 'text', '--effort', 'high']
result = subprocess.run(cmd, cwd=r'C:\99_self\conductor\conductor-ai', capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=180)
print('returncode=', result.returncode)
print('stdout=', result.stdout[:4000])
print('stderr=', result.stderr[:2000])
