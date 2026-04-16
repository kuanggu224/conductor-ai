import subprocess
prompt = 'You are a product designer. Output a short markdown requirement design for a lightweight task center. Include goals, assumptions, approach, deliverables, risks.'
cmd = [r'C:\Users\cxs96\AppData\Roaming\npm\claude.CMD', '-p', prompt, '--output-format', 'text', '--effort', 'high']
result = subprocess.run(cmd, cwd=r'C:\99_self\conductor\conductor-ai', capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=180)
print('code=', result.returncode)
print(result.stdout[:1600])
print(result.stderr[:400])
