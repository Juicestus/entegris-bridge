import os
import sys

path = '../Source'
honly = '-h' in sys.argv

write = print
if '-f' in sys.argv:
    out = open('dump.md', 'w')
    write = lambda s: out.write(s + '\n')

files = os.listdir(path)
write('# Current Codebase State\n')
if honly:
    write('Header files only\n')
for file in files:
    ext = file.split('.')[-1]
    if honly and ext != 'h': continue
    with open(path + '/' + file, 'r') as f:
        code = f.read()
        write(f'''## {file}

```cpp
{code}
``` 
''')

    
   