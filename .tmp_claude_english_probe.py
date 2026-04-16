import subprocess, json
prompt = '''You are the designer agent in Conductor.
Create an overall requirement and design brief with implementation boundaries.
Work item kind: design_overview
Stage: design
Acceptance checklist:
- Output key design points
- Clarify the implementation boundary for the next stage
Return concise markdown in English.
Use these sections: Goals, Assumptions, Approach, Deliverables, Risks.'''
cmd = [r'C:\Users\cxs96\AppData\Roaming\npm\claude.CMD', '-p', prompt, '--output-format', 'text', '--effort', 'high']
result = subprocess.run(cmd, cwd=r'C:\99_self\conductor\conductor-ai', capture_output=True, text=True, encoding='utf-8', errors='replace', timeout=180)
print(json.dumps({'code': result.returncode, 'stdout': result.stdout[:2000], 'stderr': result.stderr[:500]}, ensure_ascii=False, indent=2))
